# 실행과 재개

## 합성 확인

```sh
python3 scripts/loop.py demo --runtime ../gtm-loop-demo
python3 scripts/loop.py report --runtime ../gtm-loop-demo
```

데모는 모델 대신 고정된 합성 응답을 사용합니다. 실제 실행과 다른 별도 mode이며 실적에 포함하지 않습니다. 이미 사용한 데모 폴더는 덮어쓰지 않습니다.

## 실제 Agentic Loop

1. `python3 scripts/loop.py init --runtime ../gtm-loop-private`로 외부 운영 공간을 만듭니다.
2. 그곳의 `control/context.md`에 제품의 높은 수준 설명, 허용된 자료, 조사할 문제와 실제 권한을 적습니다. `control/observations.md`에 새로운 반응과 근거를 기록합니다. 민감한 원문을 모두 넣을 필요는 없습니다.
3. 운영 시작 전에 `control/goal.json`의 목표와 기간, 기존 회사를 고정합니다. 실행 중 변경은 거절됩니다. 첫 실행 뒤 목표 변경이 필요하면 새 폴더에서 별도 실험을 시작합니다.
4. 로컬 Codex CLI에 로그인한 뒤 `python3 scripts/loop.py run --runtime ../gtm-loop-private`로 시작합니다. 필요하면 `--model MODEL_ID`를 지정합니다. 지정하지 않으면 CLI 기본 모델을 사용합니다.
5. 에이전트가 만든 작업 결과는 `iterations/`에서 검토합니다. 실제 발송·게시·결제·CRM 작업은 이 실행기의 기능이 아닙니다. 운영자는 정확한 묶음을 검토하여 승인된 실행을 따로 수행합니다.
6. 실제로 확인한 결과는 `control/verified-events.json`에, 후속 피드백은 `control/observations.md`에 기록합니다. 새 입력을 받으면 실행기가 다시 에이전트를 호출합니다.

프롬프트에는 목표·확인된 지표·최근 실행·현재 전략·새 관측이 매번 들어갑니다. 긴 원문 대신 필요한 정보를 운영 입력에 정리합니다. 한 입력 파일은 24,000자로 제한하며 초과하면 자동으로 잘라 해석하지 않고 중지합니다.

## 반복·대기·중지

기본 호출 한도는 총 200회, 호출 간 최소 간격은 300초, 대기 입력 확인 주기는 30초입니다. 한 번의 모델 실행 제한은 600초입니다. `--max-iterations`, `--interval-seconds`, `--timeout-seconds`로 조정할 수 있습니다. 호출 한도는 재시작해도 누적됩니다.

`continue`이면 이전 출력과 전략을 넘겨 다음 호출을 합니다. `waiting_for_input` 또는 `waiting_for_approval`이면 제어 입력이 달라질 때까지 모델 호출을 쉬고 기한·목표는 계속 확인합니다. `control/STOP` 파일이나 `Ctrl+C`로 중지합니다. 같은 명령으로 재개하면 기존 실행과 기록을 보존합니다. 오류가 난 호출도 한도에 포함하고 자동 재시도하지 않습니다.

에이전트는 read-only sandbox로 호출하고 개인 CLI 설정은 로드하지 않습니다. 최종 응답만 실행기가 별도 파일로 받습니다. 인증은 로컬 CLI의 기존 인증을 사용하며 이 저장소에 저장하지 않습니다. 이것이 머신 전체의 읽기 접근이나 모든 외부 연동을 완전히 격리한다는 뜻은 아닙니다. 전송 기능을 연결하려면 범위와 원천 확인을 갖춘 별도 실행 경로가 필요합니다.

프로세스가 켜져 있어야 반복합니다. 이 저장소는 OS 예약 작업이나 백그라운드 서비스를 자동 설치하지 않습니다.

## 참고

비대화형 호출, JSON schema 출력과 ephemeral 실행은 [공식 Codex 문서](https://learn.chatgpt.com/docs/non-interactive-mode)를 따릅니다. 구체적인 CLI 옵션은 [공식 명령 문서](https://learn.chatgpt.com/docs/developer-commands?surface=cli)와 설치된 `codex exec --help`에서 확인할 수 있습니다. 예제는 특정 모델을 고정하지 않습니다.
