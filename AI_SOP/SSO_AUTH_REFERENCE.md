# 사내 SSO 연동 참고

## 목적

이 문서는 과거 Flask 애플리케이션에서 사용했던 사내 SSO 연동 패턴을 향후 AI SOP Web 서비스의 인증 설계 참고자료로 보존한다.

현재 AI SOP 서비스의 Web 프레임워크를 Flask로 결정한 것은 아니다. 실제 구현 시 선택한 프레임워크의 인증 미들웨어와 세션 관리 방식에 맞게 재설계한다.

## 기존 Flask 참고 코드

```python
from hcputil.auth.sso import SSO


@app.route('/login')
@app.route('/login/<path:sub_path>')
def login(sub_path=None):
    sso = SSO(request)
    redirect_url = sso.redirect_url + url_path
    if sub_path is not None:
        redirect_url = redirect_url + sub_path

    if session.get('logFlag') != True:
        cookie = request.headers.get('cookie')
        if cookie is not None and sso.check_day_cookie(cookie) == True:
            (
                session['emp_no'],
                session['emp_name'],
                session['emp_name_en'],
                session['dept'],
                session['email'],
                session['dept_cd'],
            ) = sso.get_sso_info(request.headers.get('cookie'))
            session['logFlag'] = True

        return redirect(redirect_url)
    else:
        return redirect(redirect_url)
```

## 확인되는 사용자 정보

| 세션 키 | 의미 |
|---|---|
| `emp_no` | 사번. AI SOP 개인 영역의 서버 측 식별자로 사용 가능 |
| `emp_name` | 한글 이름 |
| `emp_name_en` | 영문 이름 |
| `dept` | 부서명 |
| `email` | 회사 이메일 |
| `dept_cd` | 부서 코드. 팀 단위 접근 권한 매핑에 사용 가능 |
| `logFlag` | 현재 애플리케이션 세션의 로그인 확인 상태 |

## AI SOP 서비스 적용 시 원칙

- `employee_id`는 브라우저 입력값이나 업로드된 Markdown 값을 신뢰하지 않고 SSO의 `emp_no`에서 결정한다.
- 개인 초안 조회·수정 권한은 `emp_no`를 기준으로 검사한다.
- 팀 문서 접근 범위는 `dept_cd`와 별도의 권한 매핑 테이블을 기준으로 결정한다.
- 원본 Cookie 값은 로그, Markdown, AI 프롬프트 또는 추적 데이터에 남기지 않는다.
- 세션 Cookie에는 사내 표준에 맞는 `Secure`, `HttpOnly`, `SameSite` 정책을 적용한다.
- `sub_path`를 redirect URL에 연결할 때는 허용된 내부 경로인지 검증해 open redirect 또는 경로 조작을 방지한다.
- `url_path`, SSO redirect 정책, 세션 저장소와 만료 시간은 실제 운영 환경에서 별도로 확정한다.

## 향후 확인 항목

- `hcputil.auth.sso.SSO`의 현재 배포 버전과 지원 Web 프레임워크
- 사내 SSO Cookie의 유효 기간과 재인증 처리 방식
- 운영 환경의 세션 저장소 요구사항
- `dept_cd`와 AI SOP 팀 권한의 매핑 기준
- 로그아웃, 세션 만료, 인사 이동 및 퇴직자 권한 회수 방식
- 개발·검증 환경에서 사용할 SSO 대체 인증 방식
