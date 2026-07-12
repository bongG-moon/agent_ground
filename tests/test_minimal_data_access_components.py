from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from lfx.custom.eval import eval_custom_component_code
from lfx.custom.utils import create_component_template


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_IDS = (
    "oracle_table_query",
    "h_api_table_request",
    "datalake_table_query",
    "goodocs_table_reader",
    "simple_api_table_request",
)
EXPECTED_INPUT_COUNTS = {
    "oracle_table_query": 7,
    "h_api_table_request": 7,
    "datalake_table_query": 16,
    "goodocs_table_reader": 6,
    "simple_api_table_request": 10,
}


def load_component(component_id: str) -> ModuleType:
    path = ROOT / "components" / component_id / f"{component_id}.py"
    spec = importlib.util.spec_from_file_location(f"test_{component_id}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Component를 불러올 수 없습니다: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def modules() -> dict[str, ModuleType]:
    return {component_id: load_component(component_id) for component_id in COMPONENT_IDS}


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        url: str = "https://api.example.com/data",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        self.headers = headers or {"Content-Type": "application/json", "Content-Length": str(len(self.content))}
        self.closed = False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **options):
        self.calls.append((method, url, options))
        if not self.responses:
            raise AssertionError("준비한 가짜 응답이 없습니다.")
        return self.responses.pop(0)


class FakeCursor:
    def __init__(self, *, rows, description=None, column_names=None) -> None:
        self.rows = list(rows)
        self.description = description
        self.column_names = column_names
        self.execute_calls: list[tuple[str, object]] = []
        self.fetch_sizes: list[int] = []
        self.closed = False

    def execute(self, sql: str, parameters=None) -> None:
        self.execute_calls.append((sql, parameters))

    def fetchmany(self, size: int):
        self.fetch_sizes.append(size)
        return self.rows[:size]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_components_compile_to_langflow_182_templates_and_expose_one_table() -> None:
    for component_id in COMPONENT_IDS:
        path = ROOT / "components" / component_id / f"{component_id}.py"
        source = path.read_text(encoding="utf-8")
        component_class = eval_custom_component_code(source)
        config, instance = create_component_template(
            {"code": source, "output_types": []},
            module_name=f"agent_ground.direct_data_test.{component_id}",
        )

        assert instance.__class__.__name__ == component_class.__name__
        assert len(config["field_order"]) == EXPECTED_INPUT_COUNTS[component_id]
        assert [output["types"] for output in config["outputs"]] == [["DataFrame"]]
        assert config["outputs"][0]["name"] == "data_table"
        assert config["outputs"][0]["cache"] is False
        assert config["outputs"][0]["tool_mode"] is False
        assert "테이블" in config["outputs"][0]["display_name"]
        assert "_dummy_rows" not in source


def test_oracle_uses_native_bind_limits_rows_and_closes_resources(modules: dict[str, ModuleType]) -> None:
    module = modules["oracle_table_query"]
    cursor = FakeCursor(
        rows=[(1, "A"), (2, "B"), (3, "C")],
        description=[("ID",), ("ID",)],
    )
    connection = FakeConnection(cursor)
    connect_calls: list[dict] = []

    def connect_fn(**kwargs):
        connect_calls.append(kwargs)
        return connection

    rows, columns, truncated = module.execute_oracle_query(
        dsn="db.example.com/service",
        username="reader",
        password="secret",
        sql="SELECT ID, CODE FROM SAMPLE WHERE ID = :id",
        bind_parameters={"id": 1},
        max_rows=2,
        query_timeout_seconds=45,
        connect_fn=connect_fn,
    )

    assert connect_calls == [{"dsn": "db.example.com/service", "user": "reader", "password": "secret"}]
    assert connection.call_timeout == 45_000
    assert cursor.execute_calls == [("SELECT ID, CODE FROM SAMPLE WHERE ID = :id", {"id": 1})]
    assert cursor.fetch_sizes == [3]
    assert columns == ["ID", "ID_2"]
    assert rows == [{"ID": 1, "ID_2": "A"}, {"ID": 2, "ID_2": "B"}]
    assert truncated is True
    assert cursor.closed is True and connection.closed is True


def test_oracle_read_only_guard_and_empty_table_columns(modules: dict[str, ModuleType]) -> None:
    module = modules["oracle_table_query"]
    assert module.validate_read_only_sql("WITH x AS (SELECT 1 AS A FROM dual) SELECT * FROM x;").endswith(
        "SELECT * FROM x"
    )
    with pytest.raises(ValueError, match="SELECT 또는 WITH"):
        module.validate_read_only_sql("UPDATE sample SET value = 1")
    with pytest.raises(ValueError, match="다른 문장"):
        module.validate_read_only_sql("SELECT 1 FROM dual; DELETE FROM sample")
    with pytest.raises(ValueError, match="콜론"):
        module.normalize_bind_parameters({":id": 1})

    table = module.build_dataframe([], ["A", "B"])
    assert list(table.columns) == ["A", "B"]
    assert len(table) == 0


def test_h_api_posts_bind_params_and_returns_selected_rows(modules: dict[str, ModuleType]) -> None:
    module = modules["h_api_table_request"]
    response = FakeResponse(
        {"data": {"row": [{"LOT_ID": "A1", "OPER": "D/A1"}, {"LOT_ID": "A1", "OPER": "B/G1"}]}},
        url="https://hapi.example.com/trace",
    )
    transport = FakeTransport([response])

    rows = module.request_h_api_table(
        "https://hapi.example.com/trace",
        "token-value",
        '["A1", "D/A1", "B/G1"]',
        "data.row",
        20,
        1,
        transport=transport,
    )

    assert rows == [{"LOT_ID": "A1", "OPER": "D/A1"}]
    method, url, options = transport.calls[0]
    assert method == "POST" and url == "https://hapi.example.com/trace"
    assert options["headers"]["h-api-token"] == "token-value"
    assert options["json"] == {"bindParams": ["A1", "D/A1", "B/G1"]}
    assert options["allow_redirects"] is False and options["verify"] is True
    assert response.closed is True

    missing_path = FakeTransport([FakeResponse({"data": {}}, url="https://hapi.example.com/trace")])
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        module.request_h_api_table(
            "https://hapi.example.com/trace", "token", "[]", "data.row", transport=missing_path
        )
    with pytest.raises(ValueError, match="HTTP로 보낼 수 없습니다"):
        module.request_h_api_table("http://hapi.example.com/trace", "token", "[]", transport=FakeTransport([]))


def test_simple_api_get_post_and_security_boundaries(modules: dict[str, ModuleType]) -> None:
    module = modules["simple_api_table_request"]
    get_response = FakeResponse(
        {"data": {"items": [{"id": 1, "detail": {"state": "ok"}}, {"id": 2}] }},
        url="https://api.example.com/orders",
    )
    get_transport = FakeTransport([get_response])
    rows = module.request_api_table(
        "https://api.example.com/orders",
        "GET",
        '{"Authorization":"Bearer token"}',
        '{"factory":"FAB1"}',
        "",
        "data.items",
        30,
        1024 * 1024,
        10,
        transport=get_transport,
    )
    assert rows[0] == {"id": 1, "detail": '{"state":"ok"}'}
    assert get_transport.calls[0][2]["params"] == {"factory": "FAB1"}
    assert "json" not in get_transport.calls[0][2]

    post_response = FakeResponse({"results": [{"ok": True}]}, url="https://api.example.com/search")
    post_transport = FakeTransport([post_response])
    assert module.request_api_table(
        "https://api.example.com/search",
        "POST",
        "{}",
        "{}",
        '{"keyword":"DDR5"}',
        "results",
        transport=post_transport,
    ) == [{"ok": True}]
    assert post_transport.calls[0][2]["json"] == {"keyword": "DDR5"}

    with pytest.raises(ValueError, match="지원하지 않는"):
        module.request_api_table("https://api.example.com/data", "DELETE", transport=FakeTransport([]))
    with pytest.raises(ValueError, match="GET 요청에는"):
        module.request_api_table(
            "https://api.example.com/data", "GET", body_json='{"write":true}', transport=FakeTransport([])
        )

    redirect = FakeResponse(
        None,
        status_code=302,
        url="https://api.example.com/data",
        headers={"Location": "https://evil.example.net/data"},
    )
    with pytest.raises(ValueError, match="다른 origin"):
        module.request_api_table(
            "https://api.example.com/data", "GET", transport=FakeTransport([redirect])
        )

    changed_port = FakeResponse(
        None,
        status_code=302,
        url="https://api.example.com/data",
        headers={"Location": "https://api.example.com:8443/data"},
    )
    with pytest.raises(ValueError, match="다른 origin"):
        module.request_api_table(
            "https://api.example.com/data", "GET", transport=FakeTransport([changed_port])
        )
    with pytest.raises(ValueError, match="HTTP로 보낼 수 없습니다"):
        module.request_api_table("http://api.example.com/data", "GET", transport=FakeTransport([]))


def test_datalake_discovers_endpoint_and_uses_native_bind(modules: dict[str, ModuleType]) -> None:
    module = modules["datalake_table_query"]
    status_url = module.build_cluster_status_url(
        "https://lake.example.com/api/v4/",
        "runtime/cluster/{cluster_type}/running",
        "starrocks",
    )
    assert status_url == "https://lake.example.com/api/v4/runtime/cluster/starrocks/running"
    assert module.parse_mysql_endpoint("jdbc:mysql://starrocks.example.com:9030/analytics") == (
        "starrocks.example.com",
        9030,
        "analytics",
    )

    statuses = iter(
        [
            {"status": "STARTING"},
            {"status": "RUNNING", "endpoints": {"jdbc-external": "starrocks.example.com:9030"}},
        ]
    )
    observed_headers: list[dict] = []

    async def status_fetcher(url, headers, timeout):
        assert url == status_url and 1 <= timeout <= 5
        observed_headers.append(dict(headers))
        return next(statuses)

    async def no_sleep(_seconds):
        return None

    endpoint = asyncio.run(
        module.discover_mysql_endpoint(
            status_url=status_url,
            user_id="reader",
            token="jwt-token",
            endpoint_key="jdbc-external",
            allowed_mysql_hosts=(".example.com",),
            request_timeout_seconds=10,
            cluster_wait_seconds=5,
            poll_interval_seconds=1,
            status_fetcher=status_fetcher,
            sleep_fn=no_sleep,
        )
    )
    assert endpoint == ("starrocks.example.com", 9030, "")
    assert observed_headers[0]["Authorization"] == "Bearer jwt-token"

    cursor = FakeCursor(rows=[("202601", 98.1), ("202602", 98.4)], column_names=["MONTH", "YIELD"])
    connection = FakeConnection(cursor)
    connection_calls: list[dict] = []

    def connect_fn(**kwargs):
        connection_calls.append(kwargs)
        return connection

    rows, columns, truncated = module.execute_mysql_query(
        host="starrocks.example.com",
        port=9030,
        database="analytics",
        user_id="reader",
        token="jwt-token",
        sql="SELECT MONTH, YIELD FROM monthly WHERE MONTH >= %(from_ym)s",
        bind_parameters={"from_ym": "202601"},
        max_rows=1,
        connect_timeout_seconds=10,
        query_timeout_seconds=30,
        ssl_ca_path="C:/certs/company-ca.pem",
        connect_fn=connect_fn,
    )
    assert rows == [{"MONTH": "202601", "YIELD": 98.1}]
    assert columns == ["MONTH", "YIELD"] and truncated is True
    assert cursor.execute_calls[0][1] == {"from_ym": "202601"}
    assert cursor.fetch_sizes == [2]
    assert connection_calls[0]["allow_local_infile"] is False
    assert connection_calls[0]["ssl_ca"] == "C:/certs/company-ca.pem"
    assert connection_calls[0]["ssl_verify_cert"] is True
    assert connection_calls[0]["ssl_verify_identity"] is True
    assert connection_calls[0]["read_timeout"] == 30
    assert cursor.closed is True and connection.closed is True

    with pytest.raises(PermissionError, match="허용 목록"):
        asyncio.run(
            module.discover_mysql_endpoint(
                status_url=status_url,
                user_id="reader",
                token="jwt-token",
                endpoint_key="jdbc-external",
                allowed_mysql_hosts=(".example.com",),
                request_timeout_seconds=10,
                cluster_wait_seconds=5,
                poll_interval_seconds=1,
                status_fetcher=lambda *_: {
                    "status": "RUNNING",
                    "endpoints": {"jdbc-external": "attacker.example.net:9030"},
                },
                sleep_fn=no_sleep,
            )
        )

    with pytest.raises(ValueError, match="HTTP로 보낼 수 없습니다"):
        module.validate_api_base_url("http://lake.example.com/api/v4/")
    assert module.validate_api_base_url(
        "http://lake.example.com/api/v4/", allow_insecure_http=True
    ).startswith("http://")
    with pytest.raises(ValueError, match="OUTFILE"):
        module.validate_read_only_sql("SELECT * FROM t INTO OUTFILE '/tmp/leak'")
    with pytest.raises(ValueError, match="실행 가능한 MySQL 주석"):
        module.validate_read_only_sql("SELECT 1 /*! UNION SELECT 2 */")


def test_goodocs_adapter_contract_and_cleanup(modules: dict[str, ModuleType]) -> None:
    module = modules["goodocs_table_reader"]
    captured_auth: list[dict] = []

    class FakeGoodocs:
        def __init__(self, auth):
            captured_auth.append(dict(auth))

        def read_all(self):
            return [
                {"DATE": "2026-07-12", "QTY": 10, "ROW_ID": "remove"},
                {"DATE": "", "QTY": None, "LastUser": "remove"},
                {"DATE": "2026-07-13", "QTY": 20, "ROW_INDEX": 3},
            ]

    rows = module.read_goodocs_rows(
        "DOC-1",
        "USER-1",
        "TOKEN-SOURCE",
        "TOKEN-KEY",
        "PLAN",
        1,
        client_factory=FakeGoodocs,
    )
    assert rows == [{"DATE": "2026-07-12", "QTY": 10}]
    assert captured_auth == [
        {
            "USER_ID": "USER-1",
            "DOC_ID": "DOC-1",
            "TOKEN_SOURCE": "TOKEN-SOURCE",
            "TOKEN_KEY": "TOKEN-KEY",
            "SHEET_NAME": "PLAN",
        }
    ]

    with pytest.raises(module.GooDocsModuleNotConfiguredError, match="실제 GooDocs 모듈"):
        module.read_goodocs_rows("DOC-1", "USER-1", "SOURCE", "KEY")


def test_new_components_do_not_modify_legacy_flow_contracts() -> None:
    refs = json.loads((ROOT / "flows/reusable_data_flow/component_refs.json").read_text(encoding="utf-8"))
    referenced_ids = {item["id"] for item in refs["components"]}
    assert {"oracle_data", "h_api_data", "datalake_data", "goodocs_data"}.issubset(referenced_ids)
    assert not set(COMPONENT_IDS).intersection(referenced_ids)
