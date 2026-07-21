(() => {
    'use strict';

    const SUPPORTED_UI_LANGS = ['ko', 'en', 'ja', 'zh'];
    const UI_TEXT = {
        '글로벌 맞춤형 실시간 통역 지원 시스템': ['Global Customized Real-time Interpretation Support System', 'グローバル・カスタムリアルタイム通訳支援システム', '全球定制实时口译支持系统'],
        '⚙️ 시스템 접속 및 회의 설정': ['⚙️ System Login & Meeting Setup', '⚙️ システム接続・会議設定', '⚙️ 系统登录与会议设置'],
        '보안 인증 및 시스템 최적화를 위해 정보를 입력해주세요.': ['Enter the information below for secure access and system setup.', '安全な認証とシステム設定のため、以下の情報を入力してください。', '请输入以下信息以完成安全验证和系统设置。'],
        '관리자 ID': ['Administrator ID', '管理者ID', '管理员 ID'],
        '비밀번호': ['Password', 'パスワード', '密码'],
        '1. 회의 전문 분야 (Domain)': ['1. Meeting Domain', '1. 会議の専門分野', '1. 会议专业领域'],
        '2. 방장 언어 선택 (Admin Lang)': ['2. Administrator Language', '2. 管理者の言語', '2. 管理员语言'],
        '언어를 선택하세요': ['Select a language', '言語を選択してください', '请选择语言'],
        '3. 회의 제목 (Title)': ['3. Meeting Title', '3. 会議タイトル', '3. 会议标题'],
        '회의명 입력': ['Enter meeting title', '会議名を入力', '输入会议名称'],
        '4. 방장 이름 (Name)': ['4. Administrator Name', '4. 管理者名', '4. 管理员姓名'],
        '이름 (영문 또는 한글)': ['Name', '名前', '姓名'],
        '접속 및 회의 시작': ['Log In & Start Meeting', 'ログインして会議を開始', '登录并开始会议'],
        '👋 환영합니다': ['👋 Welcome', '👋 ようこそ', '👋 欢迎'],
        '설정을 완료해주세요.': ['Complete the settings below.', '以下の設定を完了してください。', '请完成以下设置。'],
        '1. 내 언어 선택': ['1. Select My Language', '1. 自分の言語を選択', '1. 选择我的语言'],
        '2. 내 이름 (Name)': ['2. My Name', '2. 自分の名前', '2. 我的姓名'],
        '회의 입장하기': ['Join Meeting', '会議に参加', '加入会议'],
        '📲 앱 설치': ['📲 Install App', '📲 アプリをインストール', '📲 安装应用'],
        '📽️ 빔 프로젝터 뷰': ['📽️ Projector View', '📽️ プロジェクタービュー', '📽️ 投影仪视图'],
        '🗑️ 화면정리': ['🗑️ Clear Screen', '🗑️ 画面をクリア', '🗑️ 清空屏幕'],
        '📄 기록저장': ['📄 Save Transcript', '📄 記録を保存', '📄 保存记录'],
        '🔒 로그아웃': ['🔒 Log Out', '🔒 ログアウト', '🔒 退出登录'],
        '언어 선택': ['Select Language', '言語を選択', '选择语言'],
        '🚪 나가기': ['🚪 Leave', '🚪 退出', '🚪 离开'],
        '🟢 마이크 개방됨': ['🟢 Microphone Open', '🟢 マイク使用可', '🟢 麦克风已开放'],
        '동시통역 채널 및 송출': ['Simultaneous Interpretation Channels', '同時通訳チャンネル・配信', '同声传译频道与输出'],
        '1. 회의 진행 단계 (사회자/발표자 언어 선택)': ['1. Session Language (Moderator/Speaker)', '1. 進行言語（司会者／発表者）', '1. 会议语言（主持人/发言人）'],
        '2. 통역 수신 채널 추가': ['2. Add Interpretation Channel', '2. 通訳受信チャンネルを追加', '2. 添加口译接收频道'],
        '추가': ['Add', '追加', '添加'],
        'AI 번역 엔진 & 오디오 제어': ['AI Translation Engine & Audio Controls', 'AI翻訳エンジン・音声制御', 'AI 翻译引擎与音频控制'],
        '🩺 실시간 진단': ['🩺 Live Diagnostic', '🩺 リアルタイム診断', '🩺 实时诊断'],
        '🎙️ 마이크': ['🎙️ Microphone', '🎙️ マイク', '🎙️ 麦克风'],
        '✍️ STT 인식': ['✍️ Speech Recognition', '✍️ 音声認識', '✍️ 语音识别'],
        '🧠 AI 번역': ['🧠 AI Translation', '🧠 AI翻訳', '🧠 AI 翻译'],
        '1. 현장 마이크 제어 (통역 엔진 가동)': ['1. Venue Microphone (Start Interpreter)', '1. 会場マイク（通訳開始）', '1. 会场麦克风（启动口译）'],
        '오디오 인터페이스 채널 지정': ['Select an audio input channel', '音声入力チャンネルを選択', '选择音频输入通道'],
        '마이크를 선택하세요': ['Select a microphone', 'マイクを選択してください', '请选择麦克风'],
        '▶️ 현장 마이크 켜기': ['▶️ Start Venue Microphone', '▶️ 会場マイクを開始', '▶️ 开启会场麦克风'],
        '2. 통역 엔진 & 마이크 최적화': ['2. Interpreter & Microphone Tuning', '2. 通訳・マイク最適化', '2. 口译与麦克风优化'],
        '문장을 끊고 번역을 시작하는 침묵 대기 시간입니다.': ['Silence duration before a sentence is finalized for translation.', '文を確定して翻訳を開始するまでの無音時間です。', '句子结束并开始翻译前的静音等待时间。'],
        '침묵 감지 (초)': ['Silence Detection (sec)', '無音検出（秒）', '静音检测（秒）'],
        '번역 엔진 과부하를 막기 위해 문장을 강제로 자르는 글자 수입니다.': ['Maximum characters before a sentence is split to protect the translation engine.', '翻訳エンジンの過負荷を防ぐため文を分割する文字数です。', '为防止翻译引擎过载而强制切分句子的字符数。'],
        '최대 길이 (자)': ['Maximum Length (chars)', '最大文字数', '最大长度（字符）'],
        '회의실의 웅성거림이나 기계 소음을 차단하는 문턱값입니다.': ['Threshold for filtering room chatter and mechanical noise.', '会場のざわめきや機械音を抑えるしきい値です。', '过滤会场交谈声和机械噪声的阈值。'],
        '주변잡음 차단': ['Noise Gate', 'ノイズゲート', '噪声门限'],
        '3. 전문 용어집 (GitHub 원격 동기화)': ['3. Technical Glossary (GitHub Sync)', '3. 専門用語集（GitHub同期）', '3. 专业术语表（GitHub 同步）'],
        '🐙 최신 단어장 불러오기': ['🐙 Load Latest Glossary', '🐙 最新用語集を取得', '🐙 获取最新术语表'],
        '버튼을 누르면 GitHub의 단어장이 동기화됩니다.': ['Select the button to sync the glossary from GitHub.', 'ボタンを押すとGitHubの用語集を同期します。', '点击按钮可从 GitHub 同步术语表。'],
        '4. 발표 자료 사전 학습 (PDF, DOCX, TXT)': ['4. Presentation Context (PDF, DOCX, TXT)', '4. 発表資料の文脈登録（PDF、DOCX、TXT）', '4. 演示资料上下文（PDF、DOCX、TXT）'],
        '📄 문서 업로드': ['📄 Upload Document', '📄 文書をアップロード', '📄 上传文档'],
        '발표자의 스크립트나 논문을 업로드하면 번역 AI가 문맥을 미리 파악합니다.': ['Upload a script or paper to provide context for translation.', '原稿や論文をアップロードすると翻訳の参考文脈として使用します。', '上传讲稿或论文，为翻译提供参考上下文。'],
        '좌장(사회자) 통제 센터': ['Moderator Control Center', '座長（司会者）コントロール', '主席（主持人）控制中心'],
        '🙋‍♂️ 청중 발언 요청 대기열': ['🙋‍♂️ Audience Speaking Requests', '🙋‍♂️ 聴衆の発言申請', '🙋‍♂️ 听众发言申请'],
        '대기 중인 요청이 없습니다.': ['No pending requests.', '保留中の申請はありません。', '没有待处理的申请。'],
        '대기 중인 발언 요청이 없습니다.': ['No pending speaking requests.', '保留中の発言申請はありません。', '没有待处理的发言申请。'],
        '🧹 청중 발언권 일괄 회수': ['🧹 Revoke All Audience Speaking Rights', '🧹 聴衆の発言権を一括取消', '🧹 撤销所有听众发言权'],
        '🛑 전체 음소거': ['🛑 Mute All', '🛑 全員ミュート', '🛑 全部静音'],
        '✔️ 🛑 토론 전체 음소거 (작동중)': ['✔️ 🛑 Discussion Muted (active)', '✔️ 🛑 討論を全員ミュート（作動中）', '✔️ 🛑 讨论全部静音（已启用）'],
        '🟢 토론 음소거 해제': ['🟢 Unmute Discussion', '🟢 討論のミュート解除', '🟢 取消讨论静音'],
        '✔️ 🟢 토론 음소거 해제 (작동중)': ['✔️ 🟢 Discussion Unmuted (active)', '✔️ 🟢 討論ミュート解除（作動中）', '✔️ 🟢 讨论取消静音（已启用）'],
        '🛑 토론 전체 음소거': ['🛑 Mute Discussion', '🛑 討論を全員ミュート', '🛑 将讨论全部静音'],
        '✔️ 🟢 음소거 해제': ['✔️ 🟢 Unmute', '✔️ 🟢 ミュート解除', '✔️ 🟢 取消静音'],
        '모든 참가자의 번역창에서 번역 음성 듣기 버튼을 표시하거나 숨깁니다.': ['Show or hide the translation audio button for all participants.', '全参加者の翻訳画面で読み上げボタンを表示または非表示にします。', '显示或隐藏所有参与者翻译窗口中的语音按钮。'],
        '🔊 번역 음성 듣기': ['🔊 Translation Audio', '🔊 翻訳読み上げ', '🔊 翻译语音'],
        '참가자 번역창의 음성 버튼 표시': ['Show audio buttons in participant windows', '参加者画面に読み上げボタンを表示', '在参与者窗口显示语音按钮'],
        '패널 및 청중 접속 현황': ['Panelist & Audience Connections', 'パネリスト・聴衆の接続状況', '嘉宾与听众连接状态'],
        '접속 중인 작업자가 없습니다.': ['No participants are connected.', '接続中の参加者はいません。', '当前没有参与者连接。'],
        '🎙️ [토론]': ['🎙️ [Panelist]', '🎙️［討論］', '🎙️【讨论】'],
        '👀 [청취]': ['👀 [Audience]', '👀［聴講］', '👀【听众】'],
        '📱 스마트폰 간편 접속 및 권한 부여': ['📱 Mobile Access & Permissions', '📱 スマートフォン接続・権限付与', '📱 手机接入与权限授予'],
        '👀 청중(뷰어) QR': ['👀 Audience QR', '👀 聴衆用QR', '👀 听众二维码'],
        '🎙️ 토론자(패널) QR': ['🎙️ Panelist QR', '🎙️ パネリスト用QR', '🎙️ 嘉宾二维码'],
        '[ ⏸️ 시스템 대기 중 ] 마이크를 켜면 통역이 시작됩니다.': ['[ ⏸️ System Ready ] Start the microphone to begin interpretation.', '[ ⏸️ システム待機中 ] マイクを開始すると通訳が始まります。', '[ ⏸️ 系统待机 ] 开启麦克风后开始口译。'],
        'ON: 뱃지 점멸로 발표자 페이스 조절 유도 / OFF: 사회자 팝업으로만 은밀히 전송': ['ON: show pacing cues to the speaker / OFF: show them only to the moderator', 'ON：発表者にもペース通知／OFF：司会者のみに通知', '开启：向发言人显示节奏提示／关闭：仅向主持人显示'],
        '🎓 발표자 리허설 모드': ['🎓 Speaker Rehearsal Mode', '🎓 発表者リハーサルモード', '🎓 发言人彩排模式'],
        '상세 원인 보기': ['View Details', '詳細を表示', '查看详细原因'],
        '추가 상세 정보가 없습니다.': ['No additional details are available.', '追加の詳細情報はありません。', '没有更多详细信息。'],
        '⏸️ 문장 간 숨 고르기': ['⏸️ Pause Between Sentences', '⏸️ 文の間で一呼吸', '⏸️ 句间停顿'],
        '🎙️ 발음 명확히 (마이크)': ['🎙️ Speak Clearly', '🎙️ 明瞭に発音', '🎙️ 清晰发音'],
        '🐢 말하는 속도 조절': ['🐢 Slow Down', '🐢 話す速度を調整', '🐢 调整语速'],
        '✂️ 한 문장 길이 단축': ['✂️ Shorten the Sentence', '✂️ 一文を短く', '✂️ 缩短句子'],
        '👆 상단의': ['👆 Use the top', '👆 上部の', '👆 请使用顶部'],
        '[🌐 언어 선택]': ['[🌐 Select Language]', '[🌐 言語選択]', '[🌐 语言选择]'],
        '메뉴를 눌러': ['menu and', 'メニューから', '菜单并'],
        '자신의 모국어를 선택해주세요.': ['select your language.', '自分の言語を選択してください。', '选择您的语言。'],
        '내가 한 말 실시간 인식 ✍️': ['My Live Speech Recognition ✍️', '自分の音声をリアルタイム認識 ✍️', '我的实时语音识别 ✍️'],
        '대기 중...': ['Waiting...', '待機中...', '等待中...'],
        '🙋‍♂️ 발언 요청하기': ['🙋‍♂️ Request to Speak', '🙋‍♂️ 発言を申請', '🙋‍♂️ 申请发言'],
        '🎤 마이크 ON (터치시 Mute)': ['🎤 Microphone ON (tap to mute)', '🎤 マイクON（タップでミュート）', '🎤 麦克风开启（点击静音）'],
        '🔴 발언 중 (터치하여 종료)': ['🔴 Speaking (tap to stop)', '🔴 発言中（タップで終了）', '🔴 正在发言（点击结束）'],
        '✕ 프로젝터 모드 종료': ['✕ Exit Projector Mode', '✕ プロジェクターモード終了', '✕ 退出投影模式'],
        '스캔하세요': ['Scan This Code', 'コードをスキャン', '请扫描二维码'],
        '별도 설치 없이 즉시 접속됩니다.': ['Join instantly without a separate installation.', '別途インストールせずに参加できます。', '无需另行安装即可立即加入。'],
        '닫기': ['Close', '閉じる', '关闭'],
        '헤더를 드래그하여 이동하거나 우측 하단을 끌어 크기를 조절하세요. (더블클릭: 최대화)': ['Drag the header to move; drag the lower-right corner to resize. (Double-click: maximize)', 'ヘッダーをドラッグして移動、右下をドラッグしてサイズ変更できます。（ダブルクリック：最大化）', '拖动标题栏可移动，拖动右下角可调整大小。（双击：最大化）'],
        '창 최대화/복원': ['Maximize/restore window', 'ウィンドウを最大化／復元', '最大化/还原窗口'],
        '이 언어 번역 닫기': ['Close this language', 'この言語の翻訳を閉じる', '关闭此语言翻译'],
        '환영합니다!': ['Welcome!', 'ようこそ！', '欢迎！'],
        '먼저 사용할 마이크를 선택해주세요.': ['Select a microphone first.', '使用するマイクを先に選択してください。', '请先选择要使用的麦克风。'],
        '이 브라우저는 마이크 진단을 지원하지 않습니다.': ['This browser does not support microphone diagnostics.', 'このブラウザはマイク診断に対応していません。', '此浏览器不支持麦克风诊断。'],
        '실제 번역 시험을 위해 원문 언어와 다른 통역 채널을 하나 이상 추가해주세요.': ['Add at least one interpretation channel different from the source language.', '実際の翻訳テストには、原文と異なる言語を1つ以上追加してください。', '请至少添加一个不同于原语言的口译频道。'],
        '현재 마이크 연결 상태가 올바르지 않습니다. 마이크를 껐다가 다시 시도해주세요.': ['The microphone connection is not ready. Turn it off and try again.', 'マイク接続が正常ではありません。一度停止して再試行してください。', '当前麦克风连接异常，请关闭后重试。'],
        '⏳ 진단 중': ['⏳ Diagnosing', '⏳ 診断中', '⏳ 正在诊断'],
        '실시간 진단 실패': ['Live Diagnostic Failed', 'リアルタイム診断失敗', '实时诊断失败'],
        '진단 단계 확인 필요': ['Check the failed diagnostic stage', '失敗した診断段階を確認してください', '请检查失败的诊断阶段'],
        '[ 🩺 실시간 진단 ] 선택한 마이크에 ‘마이크 시험입니다. 번역 기능을 확인합니다.’라고 말해주세요.': ['[ 🩺 Live Diagnostic ] Say, “This is a microphone test. I am checking the translation.”', '[ 🩺 リアルタイム診断 ] 選択したマイクに「マイクテストです。翻訳機能を確認します」と話してください。', '[ 🩺 实时诊断 ] 请对所选麦克风说：“这是麦克风测试，我正在检查翻译功能。”'],
        '25초 안에 테스트 문장을 또렷하게 말해주세요.': ['Clearly say the test sentence within 25 seconds.', '25秒以内にテスト文を明瞭に話してください。', '请在 25 秒内清晰说出测试句。'],
        '선택한 마이크에서 음성 신호가 감지되지 않았습니다.': ['No audio signal was detected from the selected microphone.', '選択したマイクから音声信号を検出できませんでした。', '未从所选麦克风检测到音频信号。'],
        '마이크 신호는 감지됐지만 STT 결과가 도착하지 않았습니다.': ['Microphone audio was detected, but no speech-recognition result arrived.', 'マイク信号は検出しましたが、音声認識結果を受信できませんでした。', '检测到麦克风信号，但未收到语音识别结果。'],
        'STT는 정상이나 다국어 번역 결과가 도착하지 않았습니다.': ['Speech recognition succeeded, but no multilingual translation arrived.', '音声認識は成功しましたが、多言語翻訳結果を受信できませんでした。', '语音识别正常，但未收到多语言翻译结果。'],
        '[ 🩺 실시간 진단 ] STT 통과 — 실제 번역 응답을 기다리는 중...': ['[ 🩺 Live Diagnostic ] Speech recognition passed — waiting for translation...', '[ 🩺 リアルタイム診断 ] 音声認識合格 — 翻訳結果を待っています...', '[ 🩺 实时诊断 ] 语音识别通过——正在等待翻译结果...'],
        '마이크 또는 통역 서버 연결을 시작하지 못했습니다.': ['Unable to start the microphone or interpretation connection.', 'マイクまたは通訳サーバーへの接続を開始できませんでした。', '无法启动麦克风或口译服务器连接。'],
        '✅ 마이크 → STT → 다국어 번역 실시간 진단을 통과했습니다.': ['✅ Microphone → STT → translation diagnostic passed.', '✅ マイク → 音声認識 → 多言語翻訳の診断に合格しました。', '✅ 麦克风 → 语音识别 → 多语言翻译诊断通过。'],
        '[ 🔴 ON AIR ] 실시간 AI 통역 엔진 가동 중...': ['[ 🔴 ON AIR ] Live AI interpretation is running...', '[ 🔴 ON AIR ] リアルタイムAI通訳を実行中...', '[ 🔴 ON AIR ] 实时 AI 口译运行中...'],
        '⏳ Gist에서 최신 단어장을 불러오는 중...': ['⏳ Loading the latest glossary...', '⏳ 最新用語集を読み込み中...', '⏳ 正在加载最新术语表...'],
        '✅ 최신 Gist 단어장이 성공적으로 동기화되었습니다.': ['✅ The latest glossary was synchronized.', '✅ 最新用語集を同期しました。', '✅ 最新术语表同步成功。'],
        '❌ Gist 단어장 동기화에 실패했습니다. 관리자에게 문의하세요.': ['❌ Glossary synchronization failed. Contact the administrator.', '❌ 用語集の同期に失敗しました。管理者に連絡してください。', '❌ 术语表同步失败，请联系管理员。'],
        '⏳ 문서 문맥 준비 중... 잠시만 기다려주세요.': ['⏳ Preparing document context... Please wait.', '⏳ 文書の参考文脈を準備中です...', '⏳ 正在准备文档上下文，请稍候...'],
        '이제 AI가 이 문서의 문맥을 참고하여 통역합니다.': ['The AI will now use this document as translation context.', 'AIはこの文書を通訳の参考文脈として使用します。', 'AI 现在会将此文档用作翻译上下文。'],
        '✅ 문서 문맥 준비가 완료되었습니다.': ['✅ Document context is ready.', '✅ 文書の参考文脈を登録しました。', '✅ 文档上下文已准备就绪。'],
        '문서 업로드 실패': ['Document Upload Failed', '文書アップロード失敗', '文档上传失败'],
        '문서 문맥 준비 완료': ['Document context is ready.', '文書の参考文脈を登録しました。', '文档上下文已准备就绪。'],
        '알 수 없는 시스템 거부': ['Unknown server response', '不明なサーバー応答', '未知服务器响应'],
        '📽️ 빔 프로젝터 뷰가 실행되었습니다.': ['📽️ Projector View started.', '📽️ プロジェクタービューを開始しました。', '📽️ 投影仪视图已启动。'],
        '마이크 사용 권한을 허용한 뒤 다시 시도해주세요.': ['Allow microphone access and try again.', 'マイクへのアクセスを許可して再試行してください。', '请允许麦克风权限后重试。'],
        '관리자 아이디와 비밀번호를 입력해주세요.': ['Enter the administrator ID and password.', '管理者IDとパスワードを入力してください。', '请输入管理员 ID 和密码。'],
        '🎙️ 토론자로 입장': ['🎙️ Join as Panelist', '🎙️ パネリストとして参加', '🎙️ 以嘉宾身份加入'],
        '👀 청중으로 입장': ['👀 Join as Audience', '👀 聴衆として参加', '👀 以听众身份加入'],
        '실시간 현장 회의': ['Live Meeting', 'リアルタイム会議', '实时会议'],
        '방장 언어를 먼저 선택해주세요.': ['Select the administrator language first.', '管理者の言語を先に選択してください。', '请先选择管理员语言。'],
        '회의 제목을 입력해주세요.': ['Enter a meeting title.', '会議タイトルを入力してください。', '请输入会议标题。'],
        '방장 이름(Name)을 입력해주세요.': ['Enter the administrator name.', '管理者名を入力してください。', '请输入管理员姓名。'],
        '서버와 연결할 수 없습니다.': ['Unable to connect to the server.', 'サーバーに接続できません。', '无法连接服务器。'],
        '서버 로그인 환경변수가 설정되지 않았습니다.': ['Server login configuration is missing.', 'サーバーのログイン設定がありません。', '服务器登录配置缺失。'],
        '아이디 또는 비밀번호가 틀렸습니다.': ['The ID or password is incorrect.', 'IDまたはパスワードが正しくありません。', 'ID 或密码错误。'],
        '지원하지 않는 파일 형식입니다.': ['Unsupported file format.', '対応していないファイル形式です。', '不支持的文件格式。'],
        '언어를 먼저 선택해주세요.': ['Select a language first.', '言語を先に選択してください。', '请先选择语言。'],
        '이름(Name)을 입력해주세요.': ['Enter your name.', '名前を入力してください。', '请输入姓名。'],
        '회의에서 나가시겠습니까?': ['Leave the meeting?', '会議から退出しますか？', '确定离开会议吗？'],
        '발표자(기본) 언어 창은 항상 첫 번째 위치에 고정됩니다.': ['The primary speaker-language window remains first.', '発表者の基本言語ウィンドウは常に先頭です。', '发言人主语言窗口始终固定在首位。'],
        '통역 엔진 언어 변경 중... (자동 재연결)': ['Changing interpretation language... (automatic reconnect)', '通訳言語を変更中...（自動再接続）', '正在切换口译语言……（自动重连）'],
        '통역 엔진 전환': ['Interpreter Switching', '通訳エンジン切替', '口译引擎切换'],
        '선택한 발표 언어로 통역 엔진을 다시 연결합니다.': ['Reconnecting with the selected speaker language.', '選択した発表言語で再接続します。', '正在使用所选发言语言重新连接。'],
        '🌍 다국어 자동 탐색 (AI 혼합 모드)': ['🌍 Automatic Language Detection (AI Mixed Mode)', '🌍 言語自動検出（AI混合モード）', '🌍 自动语言检测（AI 混合模式）'],
        '창 최대화': ['Maximize', '最大化', '最大化'],
        '이전 크기로 복원': ['Restore', '元のサイズに戻す', '恢复原大小'],
        '🎙️ 토론자(패널)용 QR코드': ['🎙️ Panelist QR Code', '🎙️ パネリスト用QRコード', '🎙️ 嘉宾二维码'],
        '스캔 즉시 상시 마이크 권한을 획득합니다.': ['Scan to join with panelist microphone access.', 'スキャンするとパネリストのマイク権限で参加します。', '扫描后以嘉宾麦克风权限加入。'],
        '👀 청중(뷰어)용 QR코드': ['👀 Audience QR Code', '👀 聴衆用QRコード', '👀 听众二维码'],
        '스캔 후 자신의 모국어를 1번만 선택하세요.': ['Scan and select your preferred language once.', 'スキャン後、自分の言語を選択してください。', '扫描后选择您的语言。'],
        '모든 청중의 발언권이 회수되었습니다.': ['All audience speaking rights were revoked.', '聴衆全員の発言権を取り消しました。', '已撤销所有听众的发言权。'],
        '서버와 연결되어 있지 않습니다.': ['Not connected to the server.', 'サーバーに接続されていません。', '未连接服务器。'],
        '⏳ 승인 대기 중 (터치 취소)': ['⏳ Awaiting Approval (tap to cancel)', '⏳ 承認待ち（タップで取消）', '⏳ 等待批准（点击取消）'],
        '사회자에게 발언을 요청했습니다.': ['Speaking request sent to the moderator.', '司会者に発言を申請しました。', '已向主持人申请发言。'],
        '발언 요청을 취소했습니다.': ['Speaking request canceled.', '発言申請を取り消しました。', '已取消发言申请。'],
        '✅ 마이크가 활성화되었습니다. (주변 잡음 자동 차단 중)': ['✅ Microphone activated. (Noise filtering is active)', '✅ マイクを有効にしました。（ノイズ抑制中）', '✅ 麦克风已启用。（正在过滤环境噪声）'],
        '마이크 접근 권한이 필요합니다.': ['Microphone permission is required.', 'マイクへのアクセス許可が必要です。', '需要麦克风权限。'],
        '🛑 사회자에 의해 토론 전체 음소거 됨': ['🛑 Discussion muted by the moderator', '🛑 司会者が討論を全員ミュートしました', '🛑 主持人已将讨论全部静音'],
        '🟢 마이크 개방됨 (자유롭게 발언하세요)': ['🟢 Microphone open (you may speak)', '🟢 マイク使用可（発言できます）', '🟢 麦克风已开放（可以发言）'],
        '🎙️ 내가 발언 중입니다 (바닥권 획득)': ['🎙️ You are speaking', '🎙️ 自分が発言中です', '🎙️ 您正在发言'],
        '🔒 다른 사람이 발언 중입니다 (잠시 대기)': ['🔒 Another person is speaking (please wait)', '🔒 他の人が発言中です（お待ちください）', '🔒 他人正在发言（请稍候）'],
        '🔇 내 마이크 꺼짐 (터치하여 켜기)': ['🔇 My Microphone OFF (tap to unmute)', '🔇 自分のマイクOFF（タップでON）', '🔇 我的麦克风已关闭（点击开启）'],
        '정말로 화면을 초기화하시겠습니까? 저장되지 않은 회의록은 유실됩니다.': ['Clear the screen? Unsaved transcript data will be lost.', '画面を初期化しますか？未保存の記録は失われます。', '确定清空屏幕吗？未保存的会议记录将丢失。'],
        '화면과 로컬 저장소가 깔끔하게 초기화되었습니다.': ['The screen and local transcript were cleared.', '画面とローカル記録を消去しました。', '屏幕和本地记录已清空。'],
        '다운로드할 회의록 내용이 없습니다.': ['There is no transcript to save.', '保存する会議記録がありません。', '没有可保存的会议记录。'],
        '⚠️ 로컬 저장소 용량이 초과되었습니다! 즉시 파일로 저장을 시도합니다.': ['⚠️ Local storage is full. Attempting to save a file now.', '⚠️ ローカル保存容量を超えました。ファイル保存を試みます。', '⚠️ 本地存储已满，正在尝试保存文件。'],
        '📄 회의록 파일 생성 및 다운로드 중...': ['📄 Preparing transcript file...', '📄 会議記録ファイルを作成中...', '📄 正在生成会议记录文件...'],
        '✅ 지정하신 위치에 회의록이 안전하게 저장되었습니다.': ['✅ Transcript saved to the selected location.', '✅ 指定した場所に会議記録を保存しました。', '✅ 会议记录已保存到所选位置。'],
        '✅ 회의록 다운로드가 완료되었습니다. 다운로드 폴더를 확인하세요.': ['✅ Transcript downloaded. Check your Downloads folder.', '✅ 会議記録をダウンロードしました。', '✅ 会议记录下载完成，请查看下载文件夹。'],
        '💾 저장이 사용자에 의해 취소되었습니다. (데이터는 유지됨)': ['💾 Save canceled. Your data is retained.', '💾 保存をキャンセルしました。データは保持されています。', '💾 保存已取消，数据仍保留。'],
        '❌ 회의록 저장 실패': ['❌ Transcript Save Failed', '❌ 会議記録の保存失敗', '❌ 会议记录保存失败'],
        '이미 추가된 언어 화면입니다.': ['That language channel is already open.', 'その言語チャンネルは既に追加されています。', '该语言频道已添加。'],
        '사회자가 번역 음성 듣기를 껐습니다.': ['The moderator disabled translation audio.', '司会者が翻訳読み上げを無効にしました。', '主持人已关闭翻译语音。'],
        '이 기기는 음성 합성을 지원하지 않습니다.': ['This device does not support speech synthesis.', 'この端末は音声合成に対応していません。', '此设备不支持语音合成。'],
        '💾 회의록 용량이 90%에 도달하여 자동으로 저장을 시작합니다.': ['💾 Transcript storage reached 90%. Automatic save is starting.', '💾 記録容量が90%に達したため自動保存を開始します。', '💾 会议记录容量达到 90%，开始自动保存。'],
        '회수': ['Revoke', '取消', '撤销'],
        '정상 종료': ['Normal closure', '正常終了', '正常关闭'],
        '네트워크 비정상 종료': ['Network interruption', 'ネットワーク異常終了', '网络异常中断'],
        '인증 만료/접근 거부': ['Authentication expired/access denied', '認証期限切れ／アクセス拒否', '身份验证过期/访问被拒绝'],
        '서버 에러': ['Server error', 'サーバーエラー', '服务器错误'],
        '서버 재시작': ['Server restart', 'サーバー再起動', '服务器重启'],
        '알 수 없는 연결 종료': ['Unknown connection closure', '不明な接続終了', '未知连接关闭'],
        '통역 연결 복구 중': ['Restoring Interpretation Connection', '通訳接続を復旧中', '正在恢复口译连接'],
        '🛑 자동 재연결이 취소되었습니다. 마이크를 확인 후 다시 켜주세요.': ['🛑 Automatic reconnect canceled. Check the microphone and start again.', '🛑 自動再接続を中止しました。マイクを確認して再開してください。', '🛑 自动重连已取消，请检查麦克风后重新开启。'],
        '마이크가 선택되지 않았습니다.': ['No microphone is selected.', 'マイクが選択されていません。', '未选择麦克风。'],
        '🛑 마이크 끄기': ['🛑 Stop Microphone', '🛑 マイクを停止', '🛑 关闭麦克风'],
        '마이크를 열 수 없습니다.': ['Unable to open the microphone.', 'マイクを開けません。', '无法开启麦克风。'],
        '선택한 마이크를 열 수 없습니다.': ['Unable to open the selected microphone.', '選択したマイクを開けません。', '无法开启所选麦克风。'],
        '마이크 연결 실패': ['Microphone Connection Failed', 'マイク接続失敗', '麦克风连接失败'],
        '마이크 장치 종료': ['Microphone Device Stopped', 'マイクデバイス停止', '麦克风设备已停止'],
        '운영체제에서 마이크가 분리되었습니다.': ['The microphone was disconnected by the operating system.', 'OSによりマイクが切断されました。', '操作系统已断开麦克风。'],
        '녹음기 시작 실패': ['Recorder Start Failed', '録音開始失敗', '录音器启动失败'],
        '브라우저 미지원 형식': ['Browser does not support the audio format', 'ブラウザ非対応の音声形式', '浏览器不支持此音频格式'],
        '녹음기 오류': ['Recorder Error', '録音エラー', '录音器错误'],
        '자동 복구 시작': ['Starting automatic recovery', '自動復旧を開始', '开始自动恢复'],
        '👀 청중(뷰어) 모드 수신 중': ['👀 Receiving in Audience Mode', '👀 聴衆モードで受信中', '👀 听众模式接收中'],
        '시스템 오류': ['System Error', 'システムエラー', '系统错误'],
        '처리 중 오류가 발생했습니다.': ['An error occurred during processing.', '処理中にエラーが発生しました。', '处理过程中发生错误。'],
        '번역 결과 누락': ['Missing Translation Result', '翻訳結果の欠落', '翻译结果缺失'],
        '번역 처리 실패': ['Translation Processing Failed', '翻訳処理失敗', '翻译处理失败'],
        '번역 처리 중 복구할 수 없는 오류가 발생했습니다.': ['An unrecoverable translation error occurred.', '翻訳処理中に復旧できないエラーが発生しました。', '翻译过程中发生无法恢复的错误。'],
        '✅ 대기 중...': ['✅ Ready...', '✅ 待機中...', '✅ 等待中...'],
        '❌ Azure API Key가 설정되지 않았습니다.': ['❌ Azure API key is not configured.', '❌ Azure APIキーが設定されていません。', '❌ 未配置 Azure API 密钥。'],
        '🌐 글로벌 다국어 식별 가동 중...': ['🌐 Automatic language detection is running...', '🌐 多言語自動識別を実行中...', '🌐 多语言自动识别运行中...'],
        '⏳ 다국어 번역 중...': ['⏳ Translating...', '⏳ 多言語翻訳中...', '⏳ 正在进行多语言翻译...'],
        '🚀 첨단 언어 모드 가동 중...': ['🚀 Speech recognition is running...', '🚀 音声認識を実行中...', '🚀 语音识别运行中...'],
        'STT 연결 오류': ['Speech Recognition Connection Error', '音声認識接続エラー', '语音识别连接错误'],
        '음성 인식 서버 연결이 종료되어 브라우저가 자동 재연결합니다.': ['The speech-recognition connection ended. The browser will reconnect automatically.', '音声認識サーバーとの接続が終了したため、自動再接続します。', '语音识别服务器连接已断开，浏览器将自动重连。'],
        'STT 연결 종료': ['Speech Recognition Disconnected', '音声認識接続終了', '语音识别连接已断开'],
        '음성 인식 연결이 종료되어 자동 복구를 시작합니다.': ['Speech recognition disconnected. Automatic recovery is starting.', '音声認識接続が終了したため、自動復旧を開始します。', '语音识别连接已断开，开始自动恢复。'],
        '🛑 사회자가 토론 전체 음소거를 실행했습니다.': ['🛑 The moderator muted the discussion.', '🛑 司会者が討論を全員ミュートしました。', '🛑 主持人已将讨论全部静音。'],
        '🟢 토론 음소거가 해제되었습니다. 자유로운 발언이 가능합니다.': ['🟢 Discussion unmuted. You may speak.', '🟢 討論のミュートを解除しました。発言できます。', '🟢 讨论已取消静音，可以发言。'],
        '🛑 사회자에 의해 발언권이 회수되었습니다.': ['🛑 The moderator revoked your speaking right.', '🛑 司会者が発言権を取り消しました。', '🛑 主持人已撤销您的发言权。'],
        '말하기 속도 초과': ['Speaking Too Fast', '話す速度が速すぎます', '语速过快'],
        '숨 고르기 필요': ['Pause Needed', '一呼吸してください', '需要停顿'],
        '마이크 발음 저하': ['Speech Clarity Low', '発音を明瞭にしてください', '发音清晰度较低'],
        '문장 길이 초과': ['Sentence Too Long', '文が長すぎます', '句子过长'],
        '번역 실패: 자동 재시도 3회 완료': ['Translation failed after 3 automatic retries', '自動再試行3回後も翻訳に失敗', '自动重试 3 次后翻译失败'],
        '🔊 듣기': ['🔊 Listen', '🔊 聞く', '🔊 播放'],
        '원문:': ['Original:', '原文：', '原文：'],
        '듣는 중 ✍️:': ['Listening ✍️:', '認識中 ✍️：', '正在识别 ✍️：'],
        '청취자': ['Audience', '聴講者', '听众'],
        '사회자': ['Moderator', '司会者', '主持人'],
        '토론자': ['Panelist', '討論者', '嘉宾'],
        '⏳ 자동 재연결 대기 중': ['⏳ Waiting to Reconnect', '⏳ 再接続待機中', '⏳ 等待重新连接'],
        '앱을 설치할 준비가 되었습니다.': ['The app is ready to install.', 'アプリをインストールできます。', '应用已可安装。'],
        '앱 설치가 완료되었습니다.': ['The app was installed.', 'アプリをインストールしました。', '应用安装完成。'],
        'iPhone/iPad에서는 공유 버튼을 누른 뒤 홈 화면에 추가를 선택하세요.': ['On iPhone/iPad, select Share, then Add to Home Screen.', 'iPhone/iPadでは共有ボタンから「ホーム画面に追加」を選択してください。', '在 iPhone/iPad 上，请点击共享，然后选择“添加到主屏幕”。'],
        '인터넷 연결이 끊겼습니다. 실시간 통역은 연결 복구 후 다시 시작됩니다.': ['You are offline. Live interpretation will resume after reconnection.', 'オフラインです。接続復旧後にリアルタイム通訳を再開します。', '网络已断开，连接恢复后将重新开始实时口译。'],
        '인터넷 연결이 복구되었습니다.': ['Internet connection restored.', 'インターネット接続が復旧しました。', '网络连接已恢复。']
    };

    const UI_PATTERNS = [
        [/^오디오 채널 (.+)$/, v => [`Audio Input ${v}`, `音声入力 ${v}`, `音频输入 ${v}`]],
        [/^\[(.+)\] 새로운 회의가 시작되었습니다\.$/, v => [`[${v}] meeting started.`, `[${v}] 会議を開始しました。`, `[${v}] 会议已开始。`]],
        [/^(.+) 채널이 추가되었습니다\.$/, v => [`${v} channel added.`, `${v} チャンネルを追加しました。`, `已添加${v}频道。`]],
        [/^(.+)초 후 선택한 마이크로 자동 재연결합니다\.$/, v => [`Reconnecting to the selected microphone in ${v} seconds.`, `${v}秒後に選択したマイクへ再接続します。`, `${v} 秒后使用所选麦克风自动重连。`]],
        [/^⏳ 자동 재연결 \((.+)초\)$/, v => [`⏳ Reconnecting (${v}s)`, `⏳ 自動再接続（${v}秒）`, `⏳ 自动重连（${v}秒）`]],
        [/^⚠️ 회의록 용량이 (.+)% 입니다\. 미리 저장해 주세요\.$/, v => [`⚠️ Transcript storage is ${v}%. Save soon.`, `⚠️ 記録容量は${v}%です。早めに保存してください。`, `⚠️ 会议记录容量为 ${v}%，请及时保存。`]],
        [/^장치: (.+)$/, v => [`Device: ${v}`, `デバイス：${v}`, `设备：${v}`]],
        [/^(.+) 결과를 받지 못했습니다\.$/, v => [`No ${v} result was received.`, `${v}の結果を受信できませんでした。`, `未收到${v}结果。`]],
        [/^실제 마이크 음성이 STT와 (.+) 번역까지 정상 처리되었습니다\.$/, v => [`Live microphone audio completed STT and ${v} translation successfully.`, `実際のマイク音声が音声認識と${v}翻訳まで正常に処理されました。`, `实际麦克风音频已成功完成语音识别和${v}翻译。`]],
        [/^\[ ✅ 실시간 진단 통과 \] (.+)$/, v => [`[ ✅ Live Diagnostic Passed ] ${v}`, `[ ✅ リアルタイム診断合格 ] ${v}`, `[ ✅ 实时诊断通过 ] ${v}`]],
        [/^\[ ❌ 실시간 진단 실패 \] (.+)$/, v => [`[ ❌ Live Diagnostic Failed ] ${v}`, `[ ❌ リアルタイム診断失敗 ] ${v}`, `[ ❌ 实时诊断失败 ] ${v}`]],
        [/^❌ 회의록 저장 중 오류가 발생했습니다: (.+)$/, v => [`❌ Transcript save error: ${v}`, `❌ 会議記録の保存エラー：${v}`, `❌ 保存会议记录时出错：${v}`]],
        [/^(.+) 번역을 3회 시도했지만 완료하지 못했습니다\.$/, v => [`${v} translation did not complete after 3 attempts.`, `${v}の翻訳は3回試行しても完了しませんでした。`, `${v}翻译尝试 3 次后仍未完成。`]],
        [/^로그인 에러: (.+)$/, v => [`Login error: ${v}`, `ログインエラー：${v}`, `登录错误：${v}`]],
        [/^문서 처리 중 오류 발생: (.+)$/, v => [`Document processing error: ${v}`, `文書処理エラー：${v}`, `文档处理错误：${v}`]],
        [/^(.+) 모드로 입장했습니다\. 환영합니다!$/, v => [`Entered in ${v}. Welcome!`, `${v}モードで参加しました。ようこそ！`, `已进入${v}模式，欢迎！`]],
        [/^🚀 기본 언어 \((.+)\) 집중 모드$/, v => [`🚀 Primary Language (${v})`, `🚀 基本言語（${v}）集中モード`, `🚀 主语言（${v}）专注模式`]],
        [/^🎙️ (.+) 집중 모드$/, v => [`🎙️ ${v} Focus Mode`, `🎙️ ${v}集中モード`, `🎙️ ${v}专注模式`]],
        [/^(.+) \(총 (.+)명\)$/, (name, count) => [`${name} (${count} total)`, `${name}（計${count}名）`, `${name}（共${count}人）`]]
    ];

    const FLAGS = {
        ko: '🇰🇷', en: '🇺🇸', zh: '🇨🇳', ja: '🇯🇵', id: '🇮🇩', vi: '🇻🇳', th: '🇹🇭', tl: '🇵🇭',
        ms: '🇲🇾', my: '🇲🇲', km: '🇰🇭', ne: '🇳🇵', hi: '🇮🇳', bn: '🇧🇩', ur: '🇵🇰', si: '🇱🇰',
        mn: '🇲🇳', uz: '🇺🇿', kk: '🇰🇿', ru: '🇷🇺', ar: '🇸🇦', tr: '🇹🇷', es: '🇪🇸', pt: '🇵🇹',
        fr: '🇫🇷', de: '🇩🇪', it: '🇮🇹', pl: '🇵🇱', uk: '🇺🇦', ro: '🇷🇴', bg: '🇧🇬', sk: '🇸🇰',
        hr: '🇭🇷', nl: '🇳🇱', sv: '🇸🇪', da: '🇩🇰', fi: '🇫🇮', el: '🇬🇷', cs: '🇨🇿', hu: '🇭🇺'
    };

    let currentUiLanguage = 'ko';
    let installPrompt = null;
    let observer = null;

    function normalizeUiLanguage(language) {
        const code = String(language || '').toLowerCase().split('-')[0];
        return SUPPORTED_UI_LANGS.includes(code) ? code : 'en';
    }

    function translationIndex(language = currentUiLanguage) {
        return { en: 0, ja: 1, zh: 2 }[normalizeUiLanguage(language)];
    }

    function uiText(source, language = currentUiLanguage) {
        if (source === null || source === undefined) return '';
        const text = String(source);
        const lang = normalizeUiLanguage(language);
        if (lang === 'ko') return text;
        const exact = UI_TEXT[text];
        if (exact) return exact[translationIndex(lang)];
        for (const [pattern, render] of UI_PATTERNS) {
            const match = text.match(pattern);
            if (match) return render(...match.slice(1))[translationIndex(lang)];
        }
        return text;
    }

    function localizedLanguageName(code, language = currentUiLanguage) {
        const lang = normalizeUiLanguage(language);
        try {
            const displayNames = new Intl.DisplayNames([lang === 'zh' ? 'zh-CN' : lang], { type: 'language' });
            return `${FLAGS[code] || '🌐'} ${displayNames.of(code) || code}`;
        } catch (_) {
            return `${FLAGS[code] || '🌐'} ${code.toUpperCase()}`;
        }
    }

    function shouldSkip(node) {
        const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
        return !parent || Boolean(parent.closest('script, style, .content-box, #qrcode, [data-no-i18n]'));
    }

    function localizeTextNode(node) {
        if (shouldSkip(node)) return;
        const raw = node.nodeValue || '';
        const match = raw.match(/^(\s*)([\s\S]*?)(\s*)$/);
        if (!match || !match[2]) return;
        if (!node.__i18nSource) node.__i18nSource = match[2];
        const translated = uiText(node.__i18nSource);
        const next = `${match[1]}${translated}${match[3]}`;
        if (raw !== next) node.nodeValue = next;
    }

    function localizeElement(element) {
        if (shouldSkip(element)) return;
        if (element.dataset && element.dataset.languageCode) {
            element.textContent = localizedLanguageName(element.dataset.languageCode);
            return;
        }
        if (element.tagName === 'OPTION' && FLAGS[element.value]) {
            element.textContent = localizedLanguageName(element.value);
            return;
        }
        for (const attr of ['placeholder', 'title', 'aria-label']) {
            if (!element.hasAttribute(attr)) continue;
            const sourceKey = `i18n${attr.replace(/(^|-)([a-z])/g, (_, __, c) => c.toUpperCase())}Source`;
            if (!element.dataset[sourceKey]) element.dataset[sourceKey] = element.getAttribute(attr);
            const translated = uiText(element.dataset[sourceKey]);
            if (element.getAttribute(attr) !== translated) element.setAttribute(attr, translated);
        }
    }

    function localizeTree(root = document.body) {
        if (!root || shouldSkip(root)) return;
        if (root.nodeType === Node.TEXT_NODE) {
            localizeTextNode(root);
            return;
        }
        if (root.nodeType !== Node.ELEMENT_NODE) return;
        localizeElement(root);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.nodeType === Node.TEXT_NODE) localizeTextNode(node);
            else localizeElement(node);
        }
    }

    function setUiLanguage(language) {
        currentUiLanguage = normalizeUiLanguage(language);
        localStorage.setItem('savedUiLanguage', currentUiLanguage);
        document.documentElement.lang = currentUiLanguage === 'zh' ? 'zh-CN' : currentUiLanguage;
        document.title = uiText('글로벌 맞춤형 실시간 통역 지원 시스템');
        localizeTree(document.body);
        document.dispatchEvent(new CustomEvent('ui-language-changed', { detail: { language: currentUiLanguage } }));
    }

    async function installPwaApp() {
        if (!installPrompt) {
            if (window.showToast) window.showToast(uiText('iPhone/iPad에서는 공유 버튼을 누른 뒤 홈 화면에 추가를 선택하세요.'));
            return;
        }
        installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        const button = document.getElementById('installAppBtn');
        if (button) button.style.display = 'none';
    }

    function initializeI18n() {
        const stored = localStorage.getItem('savedUiLanguage');
        const initial = stored || navigator.language || 'ko';
        setUiLanguage(initial);
        observer = new MutationObserver(records => {
            for (const record of records) {
                if (record.type === 'characterData') localizeTextNode(record.target);
                for (const node of record.addedNodes) localizeTree(node);
                if (record.type === 'attributes') localizeElement(record.target);
            }
        });
        observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'title', 'aria-label'] });
        const isIosInstallable = /iphone|ipad|ipod/i.test(navigator.userAgent || '') && !navigator.standalone;
        const installButton = document.getElementById('installAppBtn');
        if (installButton && (installPrompt || isIosInstallable)) installButton.style.display = 'inline-flex';
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(error => console.error('Service worker registration failed:', error));
        }
    }

    window.uiText = uiText;
    window.setUiLanguage = setUiLanguage;
    window.getUiLanguage = () => currentUiLanguage;
    window.localizedLanguageName = localizedLanguageName;
    window.installPwaApp = installPwaApp;

    window.addEventListener('beforeinstallprompt', event => {
        event.preventDefault();
        installPrompt = event;
        const button = document.getElementById('installAppBtn');
        if (button) button.style.display = 'inline-flex';
    });
    window.addEventListener('appinstalled', () => {
        installPrompt = null;
        const button = document.getElementById('installAppBtn');
        if (button) button.style.display = 'none';
        if (window.showToast) window.showToast(uiText('앱 설치가 완료되었습니다.'));
    });
    window.addEventListener('offline', () => {
        if (window.showToast) window.showToast(uiText('인터넷 연결이 끊겼습니다. 실시간 통역은 연결 복구 후 다시 시작됩니다.'), true);
    });
    window.addEventListener('online', () => {
        if (window.showToast) window.showToast(uiText('인터넷 연결이 복구되었습니다.'));
    });
    document.addEventListener('DOMContentLoaded', initializeI18n, { once: true });
})();
