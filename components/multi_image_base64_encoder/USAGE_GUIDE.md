# 다중 이미지 Base64 인코더 사용 가이드

> 상태: `user_testing`  
> 기준 환경: Langflow `1.8.2`, LFX `0.3.4`  
> 배포 방식: `multi_image_base64_encoder.py` 파일 하나를 등록하는 Standalone Component

## 목적

여러 이미지 파일을 한 번에 받아 사용자가 추가한 순서대로 Base64 또는 Data URL 목록으로 변환합니다.

다음 업무에 활용할 수 있습니다.

- 제품·불량·설비 사진을 Vision 모델 입력으로 준비
- 전·후 사진과 페이지 이미지처럼 순서가 중요한 자료 구성
- 카드뉴스·보고서 이미지 payload 생성
- 이미지 생성·편집 API에 전달할 JSON 데이터 준비

이 Component는 네트워크 요청을 보내지 않습니다. 이미지 검증과 인코딩만 수행하며, 실제 모델별 요청 구조는 다음 Component에서 조립합니다.

## 입력

| 입력 | 역할 | 기본값 |
| --- | --- | --- |
| `image_files` | 여러 이미지 파일. 추가한 순서를 유지 | 필수 |
| `output_format` | `base64` 또는 `data_url` | `base64` |
| `error_policy` | 전체 거부 또는 잘못된 파일 제외 | `reject_batch` |
| `allow_svg` | 엄격한 정적 SVG 예외 허용 | `false` |
| `max_files` | 한 번에 처리할 최대 파일 수 | 20 |
| `max_file_size_mb` | 파일 하나의 최대 크기 | 8 MB |
| `max_total_size_mb` | 인코딩 대상 전체 원본 크기 | 12 MB |

## 지원 형식

- PNG
- JPEG (`.jpg`, `.jpeg`)
- GIF
- WebP
- BMP
- TIFF (`.tif`, `.tiff`)
- SVG: 기본 차단, 고급 옵션을 켠 경우 엄격한 정적 검사 통과 파일만 허용

파일 확장자만 확인하지 않고 실제 binary signature와 확장자가 일치하는지 검사합니다.

## 출력 구조

출력은 Langflow `Data` 하나이며 내부의 `items`가 입력 순서를 유지하는 목록입니다.

```json
{
  "success": true,
  "order_preserved": true,
  "input_count": 2,
  "encoded_count": 2,
  "items": [
    {
      "index": 0,
      "position": 1,
      "filename": "before.png",
      "image_format": "png",
      "mime_type": "image/png",
      "byte_size": 12034,
      "sha256": "...",
      "encoding": "base64",
      "value": "..."
    },
    {
      "index": 1,
      "position": 2,
      "filename": "after.jpg",
      "image_format": "jpeg",
      "mime_type": "image/jpeg",
      "byte_size": 15382,
      "sha256": "...",
      "encoding": "base64",
      "value": "..."
    }
  ],
  "errors": [],
  "warnings": []
}
```

원본 로컬 경로와 이미지 bytes는 metadata·오류·status에 포함하지 않습니다. 실제 Base64/Data URL은 각 항목의 `value`에만 들어갑니다.

## 입력 순서 계약

파일명을 기준으로 다시 정렬하지 않습니다.

```text
첫 번째 업로드 -> items[0] -> index 0 -> position 1
두 번째 업로드 -> items[1] -> index 1 -> position 2
세 번째 업로드 -> items[2] -> index 2 -> position 3
```

`skip_invalid`에서 중간 파일이 제외되더라도 살아남은 항목의 `index`와 `position`은 원래 업로드 위치를 유지합니다.

## 오류 처리 방식

### 전체 거부 (`reject_batch`)

파일 하나라도 실패하면 `items`를 비우고 전체 요청을 실패로 반환합니다.

다음 단계가 모든 이미지를 반드시 필요로 할 때 사용합니다.

### 잘못된 파일 제외 (`skip_invalid`)

유효한 파일만 `items`에 남기고 제외된 위치와 사유를 `errors`에 기록합니다.

일부 이미지로도 업무를 계속할 수 있을 때 사용합니다.

## Base64 크기 주의사항

Base64 문자열은 원본 binary보다 일반적으로 약 33% 커집니다. 기본 전체 제한을 12MB로 둔 이유는 변환 후 JSON payload와 다음 API 요청의 메모리 사용량을 보수적으로 관리하기 위해서입니다.

큰 이미지나 반복 사용하는 파일은 Base64 inline 방식 대신 승인된 파일 저장소와 URL/File ID 방식을 검토합니다.

## SVG 정책

SVG는 XML 기반 active content가 될 수 있어 기본값으로 거절합니다.

`allow_svg=true`에서도 다음 항목을 차단합니다.

- script·iframe·foreignObject 등 허용 목록 밖 요소
- event handler 속성
- 외부 URL·data URL·file URL
- 외부 stylesheet·entity·DOCTYPE
- 외부 href와 동적 참조
- 과도한 node 수와 depth

이 정적 검사는 antivirus나 CDR을 대체하지 않습니다. 신뢰할 수 없는 외부 SVG는 계속 차단하는 것을 권장합니다.

## 권장 연결

```text
여러 이미지 FileInput
-> 다중 이미지 Base64 인코더
-> 모델별 Multimodal Payload Builder
-> 승인된 Vision API Component
```

또는 인코딩 결과의 `items`를 Custom Component에서 읽어 사내 API 계약에 맞게 변환합니다.

## 사용자 확인 항목

- [ ] 두 장 이상 업로드했을 때 `items` 순서가 같다.
- [ ] Base64를 decode하면 원본 bytes와 같다.
- [ ] 확장자와 실제 signature가 다르면 거절한다.
- [ ] 파일별·전체 크기 제한이 동작한다.
- [ ] `reject_batch`와 `skip_invalid`가 다르게 동작한다.
- [ ] SVG는 기본 거절되고 정적 허용 정책이 동작한다.
- [ ] status와 오류에 로컬 경로와 Base64 본문이 나타나지 않는다.

사용자 확인이 끝나기 전까지 상태는 `user_testing`입니다.
