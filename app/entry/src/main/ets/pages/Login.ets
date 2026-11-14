import { UiTheme } from '../utils/UiTheme';
import router from '@ohos.router';
import { common } from '@kit.AbilityKit';
import { AgentApiClient } from '../api/AgentApiClient';
import { ApiDefaults } from '../api/ApiDefaults';
import { ApiEnvironmentStore } from '../api/ApiEnvironmentStore';
import { AuthStore } from '../api/AuthStore';
import { AuthClient } from '../api/AuthClient';
import { AuthResult } from '../api/AuthResult';
import { AuthUser, GRADE_OPTIONS } from '../api/AuthModels';
import { appState } from '../data/AppState';

/**
 * 登录页 —— 对齐参考设计（ACCOUNT ACCESS 卡片式表单）。
 *
 * 视觉结构：
 *
 *   ┌────────────────────────────────────────┐
 *   │  ACCOUNT ACCESS                        │
 *   │  登录知学                                │
 *   │  进入你的 AI 学习工作台                   │
 *   │  ┌──────────────┬──────────────┐        │
 *   │  │  登录       │  注册        │  ← 分段切换
 *   │  └──────────────┴──────────────┘        │
 *   │  ┌──────────────┬──────────────┐        │
 *   │  │  密码登录    │  验证码登录  │  ← 分段切换（仅登录模式）
 *   │  └──────────────┴──────────────┘        │
 *   │  昵称                                    │
 *   │  [  请输入昵称                      ]  │
 *   │  密码                                    │
 *   │  [  请输入密码                   ]  │
 *   │  □ 记住我                                │
 *   │  ┌────────────────────────────────┐     │
 *   │  │          登录  →                │     │
 *   │  └────────────────────────────────┘     │
 *   │  ─────────────────────────────────      │
 *   │  [ 跳过登录，在线体验 ]                   │
 *   └────────────────────────────────────────┘
 *
 * 密码登录会请求后端 `/api/v1/auth/login`；注册时后端只保存加盐哈希，
 * 不保存或回传明文密码。验证码按服务配置开放，华为账号通过 Account Kit 授权登录。
 *
 * 「一键体验 / 跳过登录」必须保留且足够显眼：评审现场任何摩擦都是风险。
 */
@Entry
@Component
struct Login {
  @StorageProp('layoutHeightVp') windowHeight: number = 720;
  /** 'login' | 'register' —— 顶部大分段。 */
  @State mode: string = 'login';
  /** 'password' | 'code' —— 登录方式分段（仅 login 模式显示）。 */
  @State method: string = 'password';
  @State nickname: string = '';
  /** 手机号 —— 注册必填、登录首选（唯一身份标识）。 */
  @State phone: string = '';
  @State password: string = '';
  /** 短信验证码（仅 `method === 'code'` 使用）。 */
  @State verifyCode: string = '';
  /** 重发倒计时秒数；0 表示可以发送。值来自后端 `resendAfterSeconds`，不写死。 */
  @State resendCountdown: number = 0;
  /** 验证码是否已发出（用于切换按钮文案与展示开发回显提示）。 */
  @State codeSent: boolean = false;
  /**
   * 短信是否**真的**发出去了。
   *
   * false 表示后端仅在开发模式回显了验证码 —— 界面必须如实说明，
   * 不能显示成"验证码已发送"（后端专门设计了 `smsDelivered` 字段来区分）。
   */
  @State smsDelivered: boolean = false;
  /** 开发模式回显的验证码；非空时界面用醒目样式标注"开发回显"。 */
  @State devCode: string = '';
  @State grade: string = '';
  @State rememberMe: boolean = true;
  @State rememberAccount: boolean = true;
  @State busy: boolean = false;
  @State message: string = '';
  @State messageIsError: boolean = false;
  /** 华为账号一键登录进行中。 */
  @State isHuaweiLoggingIn: boolean = false;
  @State huaweiMessage: string = '';
  @State huaweiSetupHint: string = '';

  private isActive: boolean = false;

  /** 倒计时定时器句柄；离开页面必须清掉，否则会在已销毁的页面上 setState。 */
  private resendTimer: number = -1;

  aboutToAppear(): void {
    this.isActive = true;
    this.refreshHuaweiSetupHint();
    this.rememberMe = AuthStore.getKeepSignedIn();
    this.rememberAccount = AuthStore.getRememberAccount();
    const account: string = AuthStore.getRecentAccount();
    if (/^1\d{10}$/.test(account)) this.phone = account;
    else this.nickname = account;
    const params: Record<string, Object> = router.getParams() as Record<string, Object>;
    //  `router.getParams()` 在**没有路由参数**时返回 undefined —— 而"冷启动直接进登录页"
    // 正是这种情况（App 未登录时的必经路径）。
    // `as Record<string, Object>` 只是**编译期断言**，不会让运行时值变成非空对象，
    // 少了下面这道判断就会抛 TypeError。实测后果是 jscrash：
    //   Reason: TypeError / Cannot load property of null or undefined
    //   at aboutToAppear entry (entry/src/main/ets/pages/Login.ets:86:47)
    // —— **未登录状态下 App 一启动就崩**，表现为"应用打不开"。
    // 另外三处同类用法（`Index` / `ExercisePractice` / `CourseImport`）都有这道判空，这里原来漏了。
    if (params === undefined || params === null) {
      return;
    }
    const notice: Object | undefined = params['notice'];
    if (typeof notice === 'string' && notice.length > 0) {
      this.message = notice;
      this.messageIsError = false;
    }
  }

  aboutToDisappear(): void {
    this.isActive = false;
    this.stopResendTimer();
  }

  onPageShow(): void {
    this.refreshHuaweiSetupHint();
  }

  private refreshHuaweiSetupHint(): void {
    const config = AgentApiClient.getConfig();
    const hint: string = config.useFixture ? '当前为离线演示，请先切换到在线后端。' :
      (!ApiDefaults.supportsHuaweiLogin(config.baseUrl) ? (ApiDefaults.ALLOW_USB_HUAWEI_LOGIN ?
        '开发版可通过 USB 联调后端登录，也可使用设备信任的 HTTPS 后端。' :
        '华为账号登录需要设备信任的 HTTPS 后端。') : '');
    if (hint !== this.huaweiSetupHint) this.huaweiMessage = '';
    this.huaweiSetupHint = hint;
  }

  // ---------------------------------------------------------------- 校验
  /**
   * 手机号格式校验（与后端 `service.normalize_phone` 同一套规则）。
   *
   * 前端先校验是为了**减少一次无效往返**；后端仍会独立校验，
   * 不能把前端校验当作安全边界。
   */
  private validatePhone(): string {
    const digits: string = this.phone.replace(/[^\d]/g, '');
    const normalized: string = (digits.length === 13 && digits.startsWith('86'))
      ? digits.substring(2) : digits;
    if (normalized.length === 0) {
      return '请输入手机号';
    }
    if (!/^1[3-9]\d{9}$/.test(normalized)) {
      return '手机号格式不正确（应为 11 位中国大陆号码）';
    }
    return '';
  }

  /** 取出归一化后的手机号（去掉 +86、空格、横线）。 */
  private normalizedPhone(): string {
    const digits: string = this.phone.replace(/[^\d]/g, '');
    return (digits.length === 13 && digits.startsWith('86')) ? digits.substring(2) : digits;
  }

  // ---------------------------------------------------------------- 流程
  /**
   * 登录成功后进入**对话页**。
   *
   *  修正来源：原来跳 `pages/Index`，而 Index 的默认标签是「智能体」，
   * 于是「登录之后看到的第一个界面是智能体页」——与「对话式学习秘书」的定位相反。
   * 现改为直达 `pages/ChatMain`，登录后立即是对话界面。
   */
  private goHome(): void {
    router.replaceUrl({ url: 'pages/ChatMain' });
  }

  /**
   * 真实账号登录后必须走联机数据源 —— 而且必须**持久化**。
   *
   * 修复的缺陷：原先三处登录都只调 `AgentApiClient.setUseFixture(false)`，
   * 那只改内存、没写回 `ApiEnvironmentStore`。于是这条路径会出问题：
   *
   *   打开离线 Fixture（持久化为 true）→ 退出 → 登录真实账号（内存置 false）
   *   → 正常使用 → **重启 App** → `EntryAbility` 把持久化的 true 恢复回来
   *   → 已登录的账号直接走 Fixture 分支，看到的是演示数据。
   *
   * 用户视角就是「离线/联机那个开关自己又跳回离线了」。现在与
   * 「接口环境」页一样，把选择写回持久化存储。
   */
  private useOnlineSource(): void {
    AgentApiClient.setUseFixture(false);
    try {
      const context: common.UIAbilityContext =
        this.getUIContext().getHostContext() as common.UIAbilityContext;
      ApiEnvironmentStore.save(context, AgentApiClient.getConfig());
    } catch (error) {
      // 持久化失败不影响本次登录：本次会话已经是联机的，只是下次启动会按旧值。
    }
  }

  /** 使用当前配置的后端，以游客身份进入。 */
  private enterAsGuest(): void {
    if (!ApiDefaults.ALLOW_DEMO) return;
    this.useOnlineSource();
    const result: AuthResult = AuthClient.continueAsGuest();
    appState.applyLogin(result.user, 'guest');
    this.goHome();
  }

  /**
   * 离线兜底：切到 Fixture 后以游客身份进入。
   * 这是"无网络也能演示"的逃生口——评委面前绝不能出现进不去的页面。
   */
  private enterOffline(): void {
    if (!ApiDefaults.ALLOW_DEMO) return;
    AgentApiClient.setUseFixture(true);
    //  必须持久化：`setUseFixture()` 只改**内存**里的 `config.useFixture`。
    //
    // 这里原先漏了落盘，而对称的 `useOnlineSource()`（上面那个）是落盘的 ——
    // 两条路径行为不一致，表现为"从登录页选择离线演示，
    // 关掉 App 再打开又变回联机"。
    // 实测证据：`preferences/api_environment` 里的 `use_fixture` 始终是 `false`，
    // 从没被这个按钮改过。
    try {
      const context: common.UIAbilityContext =
        this.getUIContext().getHostContext() as common.UIAbilityContext;
      ApiEnvironmentStore.save(context, AgentApiClient.getConfig());
    } catch (error) {
      // 落盘失败不影响本次进入：本次会话已经是离线的，只是下次启动会按旧值。
    }
    const result: AuthResult = AuthClient.continueOffline(true);
    appState.applyLogin(result.user, 'fixture');
    this.goHome();
  }

  /**
   * 主按钮分派。
   *
   *  这里修掉了一个实际缺陷：**此前两个标签都只调 `AuthClient.register()`**，
   * 于是「登录」标签下用同一昵称再点一次会**又建一个新账号**
   * （实测两次注册「张三」得到两个不同 userId）——用户以为登录了，
   * 实际旧画像永远读不到。
   *
   * 现在按 `mode` 真正分派：
   *   · 登录 → `AuthClient.loginByPhoneOrNickname()`（手机号优先）
   *   · 注册 → `AuthClient.register()`（手机号已存在时拒绝注册）
   *
   * 密码字段参与后端注册与登录校验。
   */
  private async onSubmit(): Promise<void> {
    AuthStore.setLoginPreferences(this.normalizedPhone(), this.rememberAccount, this.rememberMe);
    if (this.busy) {
      return;
    }

    // 验证码登录：走独立链路（先校验手机号+验证码，再 verifyCode）
    if (this.method === 'code' && this.mode === 'login') {
      await this.onVerifyCodeLogin();
      return;
    }

    // 手机号登录以手机号作为唯一身份，不应再强迫用户重复填写昵称。
    // 昵称只在注册、或明确选择“按昵称登录”（手机号留空）时参与校验。
    if (this.mode === 'register' && this.nickname.trim().length > 0 && AuthClient.validateNickname(this.nickname).length > 0) {
      this.message = AuthClient.validateNickname(this.nickname); this.messageIsError = true; return;
    }
    if (this.password.length < 6) {
      this.message = '密码至少需要 6 位。';
      this.messageIsError = true;
      return;
    }

    // 手机号：注册时**必填**，登录时可选（但填了就会优先按号匹配）
    const phoneError: string = this.validatePhone();
    if (phoneError.length > 0) {
      this.message = phoneError;
      this.messageIsError = true;
      return;
    }
    if (this.mode === 'login' && this.phone.trim().length > 0 && phoneError.length > 0) {
      this.message = phoneError;
      this.messageIsError = true;
      return;
    }

    this.busy = true;
    this.message = '';
    const phone: string = this.normalizedPhone();

    // 关键分派
    let result: AuthResult;
    if (this.mode === 'register') {
      // 真实账号固定从零开始；完整示例只属于“跳过登录”的游客模式。
      result = await AuthClient.register(this.nickname, phone, this.grade, this.password);
    } else {
      // 登录且填了手机号 → 按手机号登录（唯一、可验证，比昵称可靠）
      result = await AuthClient.loginByPhone(phone, this.password);

    }
    if (!this.isActive) {
      return;
    }
    this.busy = false;

    if (!result.ok) {
      // 登录模式下"查无此账号"是很常见的误操作，直接把用户引到注册标签
      if (this.mode === 'login'
        && (result.message.indexOf('还没有账号') >= 0 || result.message.indexOf('请先注册') >= 0)) {
        this.message = '还没有账号。已为你切到「注册」，补上手机号再点一次即可建立。';
        this.messageIsError = true;
        this.mode = 'register';
        return;
      }
      this.message = result.message;
      this.messageIsError = true;
      return;
    }
    // 真实账号需要走联机数据源
    this.useOnlineSource();
    // 建号成功时把"这次到底建了什么"带到首屏去说（登录页立刻被 replaceUrl 换掉，
    // 留在这里的提示用户根本看不到）。登录已有账号时没有这句话可说。
    appState.applyLogin(result.user, result.source,
      result.createdAccount === true ? result.message : '');
    if (result.needsPasswordSetup === true) { router.replaceUrl({ url: 'pages/PasswordSetup' }); return; }
    this.goHome();
  }

  /** 华为系统授权成功后，使用后端签发的会话进入知学 Mate。 */
  private async onHuaweiLogin(): Promise<void> {
    if (this.busy) {
      return;
    }
    const context = this.getUIContext().getHostContext() as common.UIAbilityContext | undefined;
    if (!context) {
      this.huaweiMessage = '暂时无法打开华为授权，请重新进入登录页后重试。';
      return;
    }
    AuthStore.setLoginPreferences(AuthStore.getRecentAccount(), this.rememberAccount, this.rememberMe);
    this.busy = true;
    this.isHuaweiLoggingIn = true;
    this.message = '';
    this.huaweiMessage = '';
    let result: AuthResult;
    try {
      result = await AuthClient.loginWithHuaweiAccount(context, () => this.isActive);
    } catch (error) {
      result = { ok: false, user: null, source: 'local', message: '暂时无法完成华为账号登录，请重试。' };
    }
    if (!this.isActive) {
      return;
    }
    this.busy = false;
    this.isHuaweiLoggingIn = false;
    if (!result.ok) {
      this.huaweiMessage = result.message;
      return;
    }
    appState.applyLogin(result.user, result.source,
      result.createdAccount === true ? result.message : '');
    this.goHome();
  }

  // -------------------------------------------------------- 手机号验证码登录
  /**
   * 发送验证码。
   *
   * 三个要点：
   * 1. **倒计时用后端返回的 `resendAfterSeconds`**，不要写死 60 ——
   *    否则界面显示的"可重发时间"与后端真实限流不一致，用户点了会被 429。
   * 2. `smsDelivered === false` 时**必须如实告知**短信没真的发出去，
   *    并展示开发回显码。这是后端专门设计来避免"假装已发送"的字段。
   * 3. 离开页面要清定时器，避免在已销毁的页面上写 @State。
   */
  private async onSendCode(): Promise<void> {
    if (this.busy || this.resendCountdown > 0) {
      return;
    }
    const phoneError: string = this.validatePhone();
    if (phoneError.length > 0) {
      this.message = phoneError;
      this.messageIsError = true;
      return;
    }

    this.busy = true;
    this.message = '';
    const result: AuthResult = await AuthClient.sendVerificationCode(this.normalizedPhone());
    if (!this.isActive) {
      return;
    }
    this.busy = false;

    if (!result.ok) {
      this.message = result.message;
      this.messageIsError = true;
      return;
    }

    this.codeSent = true;
    this.smsDelivered = result.smsDelivered === true;
    this.devCode = result.devCode !== undefined ? result.devCode : '';
    this.message = result.message;
    // 开发回显不是错误，但要足够醒目，避免被误当成"短信已发送"
    this.messageIsError = false;
    this.startResendTimer(result.resendAfterSeconds !== undefined ? result.resendAfterSeconds : 60);
  }

  /** 校验验证码并登录 / 自动注册。 */
  private async onVerifyCodeLogin(): Promise<void> {
    AuthStore.setLoginPreferences(this.phone.trim(), this.rememberAccount, this.rememberMe);
    const phoneError: string = this.validatePhone();
    if (phoneError.length > 0) {
      this.message = phoneError;
      this.messageIsError = true;
      return;
    }
    if (!/^\d{6}$/.test(this.verifyCode.trim())) {
      this.message = '请输入 6 位数字验证码。';
      this.messageIsError = true;
      return;
    }

    this.busy = true;
    this.message = '';
    // 昵称/年级只在"首次自动建号"时被后端采用；已注册手机号会忽略它们。
    // 验证码首次建号固定为空数据，演示预置只保留给游客身份。
    const result: AuthResult = await AuthClient.verifyCode(
      this.normalizedPhone(), this.verifyCode.trim(),
      this.nickname.length > 0 ? this.nickname : undefined,
      this.grade.length > 0 ? this.grade : undefined);
    if (!this.isActive) {
      return;
    }
    this.busy = false;

    if (!result.ok) {
      this.message = result.message;
      this.messageIsError = true;
      return;
    }
    this.useOnlineSource();
    // 验证码链路可能"顺手建了个号"，建号说明同样要带到首屏（见 onSubmit）。
    appState.applyLogin(result.user, result.source,
      result.createdAccount === true ? result.message : '');
    if (result.needsPasswordSetup === true) { router.replaceUrl({ url: 'pages/PasswordSetup' }); return; }
    this.goHome();
  }

  /** 启动重发倒计时。 */
  private startResendTimer(seconds: number): void {
    this.stopResendTimer();
    this.resendCountdown = seconds > 0 ? seconds : 60;
    this.resendTimer = setInterval(() => {
      if (!this.isActive) {
        this.stopResendTimer();
        return;
      }
      this.resendCountdown = this.resendCountdown - 1;
      if (this.resendCountdown <= 0) {
        this.resendCountdown = 0;
        this.stopResendTimer();
      }
    }, 1000);
  }

  private stopResendTimer(): void {
    if (this.resendTimer !== -1) {
      clearInterval(this.resendTimer);
      this.resendTimer = -1;
    }
  }

  private canSubmit(): boolean {
    if (this.busy) {
      return false;
    }
    if (this.mode === 'register' && this.nickname.trim().length > 0 && AuthClient.validateNickname(this.nickname).length > 0) {
      return false;
    }
    if (this.validatePhone().length > 0 || this.password.length < 6 || this.password.length > 72) {
      return false;
    }
    return true;
  }

  // ---------------------------------------------------------------- 界面
  build() {
    Column() {
      Scroll() {
        // Keep the scroll viewport full width and constrain the form itself.
        Column({ space: 0 }) {
          // ---------- 标题区 ----------
          Column({ space: 6 }) {
            Text('知学 Mate').fontSize(26).fontWeight(FontWeight.Medium).fontColor(UiTheme.ink).width('100%').textAlign(TextAlign.Center)
            Text('你的智能学习伙伴')
              .fontSize(14).fontColor(UiTheme.secondary).width('100%').textAlign(TextAlign.Center)
              .margin({ top: 2 })
          }
          .width('100%').alignItems(HorizontalAlign.Start)
          .padding({ top: this.windowHeight < 480 ? 16 : 36, bottom: 26 })

          // ---------- 登录 / 注册 分段 ----------
          // 用无参 Builder（直接读 this.mode），确保切换后高亮与主按钮文案必然刷新
          this.ModeSegment()

          // ---------- 表单 ----------
          Column({ space: 16 }) {
            if (this.mode === 'register') {
            // 昵称仅用于展示
            Column({ space: 8 }) {
              Text('昵称（注册选填）').fontSize(14).fontWeight(FontWeight.Medium).fontColor(UiTheme.ink).width('100%')
              Row({ space: 10 }) {

                TextInput({ placeholder: '选填，留空自动生成昵称', text: this.nickname })
                  .layoutWeight(1).height(48).fontSize(15)
                  .backgroundColor(Color.Transparent).padding(0)
                  .maxLength(16).enabled(!this.busy)
                  .onChange((value: string) => {
                    this.nickname = value;
                    if (this.messageIsError) {
                      this.message = '';
                      this.messageIsError = false;
                    }
                  })
              }
              .width('100%').padding({ left: 14, right: 14 })
              .backgroundColor(UiTheme.subtle).borderRadius(8)
              .border({ width: 1, color: UiTheme.line })
            }.width('100%')

            }
            // 手机号是唯一登录标识
            Column({ space: 8 }) {
              Row({ space: 6 }) {
                Text('手机号').fontSize(14).fontWeight(FontWeight.Medium).fontColor(UiTheme.ink)
                Text('必填')
                  .fontSize(11)
                  .fontColor(this.mode === 'register' ? '#B91C1C' : UiTheme.muted)
                  .backgroundColor(this.mode === 'register' ? '#FEF2F2' : UiTheme.subtle)
                  .padding({ left: 6, right: 6, top: 2, bottom: 2 })
                  .borderRadius(6)
                Blank()
              }.width('100%')
              Row({ space: 10 }) {

                TextInput({ placeholder: '11 位手机号', text: this.phone })
                  .type(InputType.PhoneNumber)
                  .layoutWeight(1).height(48).fontSize(15)
                  .backgroundColor(Color.Transparent).padding(0)
                  .maxLength(20).enabled(!this.busy)
                  .onChange((value: string) => {
                    this.phone = value;
                    if (this.messageIsError) {
                      this.message = '';
                      this.messageIsError = false;
                    }
                  })
              }
              .width('100%').padding({ left: 14, right: 14 })
              .backgroundColor(UiTheme.subtle).borderRadius(8)
              .border({ width: 1, color: UiTheme.line })
              Text(this.mode === 'register'
                ? '手机号是账号的唯一标识，用于登录与找回。'
                : (this.method === 'code'
                  ? '手机号验证：已有账号进入原账号，首次验证后请设置密码。'
                  : '请填写手机号和密码，一个手机号对应一个账号。'))
                .fontSize(11).fontColor(UiTheme.muted).lineHeight(17).width('100%')
            }.width('100%')

            // ---------- 登录方式 = 验证码：验证码输入 + 获取验证码 ----------
            if (this.mode === 'login' && this.method === 'code') {
              Column({ space: 8 }) {
                Text('短信验证码').fontSize(14).fontWeight(FontWeight.Medium)
                  .fontColor(UiTheme.ink).width('100%')
                Row({ space: 10 }) {

                  TextInput({ placeholder: '6 位数字验证码', text: this.verifyCode })
                    .type(InputType.Number)
                    .maxLength(6)
                    .layoutWeight(1).height(48).fontSize(15)
                    .backgroundColor(Color.Transparent).padding(0)
                    .enabled(!this.busy)
                    .onChange((value: string) => { this.verifyCode = value; })
                  // 倒计时读的是后端 `resendAfterSeconds`，与真实限流一致
                  Button(this.resendCountdown > 0
                    ? `${this.resendCountdown} 秒后可重发`
                    : (this.codeSent ? '重新获取' : '获取验证码'))
                    .fontSize(13).height(36).borderRadius(8)
                    .backgroundColor(this.resendCountdown > 0 ? UiTheme.line : '#DBEAFE')
                    .fontColor(this.resendCountdown > 0 ? UiTheme.muted : UiTheme.accent)
                    .enabled(!this.busy && this.resendCountdown === 0)
                    .onClick(() => { this.onSendCode(); })
                }
                .width('100%').padding({ left: 14, right: 14 })
                .backgroundColor(UiTheme.subtle).borderRadius(8)
                .border({ width: 1, color: UiTheme.line })

                // 开发回显必须醒目且如实：不能让人以为短信真的发出去了
                if (!this.smsDelivered && this.devCode.length > 0) {
                  Column({ space: 4 }) {
                    Text('开发模式回显（短信未真实下发）')
                      .fontSize(12).fontWeight(FontWeight.Medium).fontColor('#B45309').width('100%')
                    Text(`验证码：${this.devCode}`)
                      .fontSize(15).fontWeight(FontWeight.Bold).fontColor('#B45309').width('100%')
                    Text('后端未配置短信服务商，此码仅用于联调与演示。')
                      .fontSize(11).fontColor('#B45309').lineHeight(16).width('100%')
                  }
                  .width('100%').padding(12).backgroundColor('#FFFBEB')
                  .borderRadius(8).border({ width: 1, color: '#FDE68A' })
                } else if (this.codeSent) {
                  Text('验证码已发送。收不到可改用「密码登录」或「一键体验」。')
                    .fontSize(11).fontColor(UiTheme.secondary).lineHeight(17).width('100%')
                }
              }.width('100%')
            }

            // 密码登录 / 注册 —— 验证码方式下不显示
            if (!(this.mode === 'login' && this.method === 'code')) {
              Column({ space: 8 }) {
                Text('密码').fontSize(14).fontWeight(FontWeight.Medium).fontColor(UiTheme.ink).width('100%')
                Row() {
                  TextInput({ placeholder: '请输入密码', text: this.password })
                    .type(InputType.Password).showPasswordIcon(true)
                    .layoutWeight(1).height(48).fontSize(15)
                    .backgroundColor(Color.Transparent).padding(0)
                    .enabled(!this.busy)
                    .onChange((value: string) => { this.password = value; })
                }
                .width('100%').padding({ left: 14, right: 14 })
                .backgroundColor(UiTheme.subtle).borderRadius(8)
                .border({ width: 1, color: UiTheme.line })

                Text(this.mode === 'register'
                  ? '至少 6 位；服务端仅保存加盐哈希，不保存明文。'
                  : '密码会提交到后端校验；服务端只保存加盐哈希。')
                  .fontSize(11).fontColor(UiTheme.muted).lineHeight(17).width('100%')
            }.width('100%')
            }

            if (this.mode === 'login') {
              Row() {
                Blank()
                Text('忘记密码？').fontSize(13).fontColor(UiTheme.accent)
                  .onClick(() => {
                    this.message = '暂未接入短信找回。请联系管理员核验账号后重置。请勿重复注册，以免生成新账号。';
                    this.messageIsError = false;
                  })
              }.width('100%')
            }

            // 年级（仅注册模式）
            if (this.mode === 'register') {
              Column({ space: 8 }) {
                Text('在读年级（可跳过）').fontSize(14).fontWeight(FontWeight.Medium)
                  .fontColor(UiTheme.ink).width('100%')
                Flex({ wrap: FlexWrap.Wrap }) {
                  ForEach(GRADE_OPTIONS, (item: string) => {
                    Text(item)
                      .fontSize(13)
                      .fontColor(this.grade === item ? UiTheme.surface : UiTheme.accent)
                      .backgroundColor(this.grade === item ? UiTheme.accent : UiTheme.tint)
                      .padding({ left: 14, right: 14, top: 7, bottom: 7 })
                      .borderRadius(8).margin({ right: 8, bottom: 8 })
                      .onClick(() => { this.grade = this.grade === item ? '' : item; })
                  })
                }.width('100%')
              }.width('100%')
            }

            if (this.mode === 'register') {
              Text('新账号将从零开始：不预置课程、知识点或学习计划，内容由你导入的资料、练习和智能体操作逐步建立。')
                .fontSize(11).fontColor(UiTheme.accent).lineHeight(17).width('100%')
              .width('100%').padding(12)
              .backgroundColor(UiTheme.subtle)
              .borderRadius(8)
              .border({ width: 1, color: UiTheme.line })
            }

            // 记住我
            Row({ space: 8 }) {
              Checkbox().select(this.rememberMe).hitTestBehavior(HitTestMode.None)
                .width(20).height(20).selectedColor(UiTheme.accent)
              Text('保持登录（下次自动进入）').fontSize(13).fontColor(UiTheme.secondary)
            }
            .width('100%')
            .onClick(() => { this.rememberMe = !this.rememberMe; })

            Row({ space: 8 }) {
              Checkbox().select(this.rememberAccount).hitTestBehavior(HitTestMode.None)
              Text('记住账号').fontSize(13).fontColor(UiTheme.secondary)
            }.width('100%').onClick(() => { this.rememberAccount = !this.rememberAccount; })

            // 主按钮
            // 文案必须与当前标签严格对应：此前两个标签下都是"登录 →"，
            // 用户以为在登录、实际在注册（见 onSubmit 的说明）。
            //  按钮**不再因为"还没填完"而变灰**。
            //
            // 原实现是 `.backgroundColor(canSubmit() ? 蓝 : 灰)` + `.enabled(canSubmit())`：
            // 注册模式下只要手机号还没填成合法的 11 位，按钮就是灰的、而且**不告诉用户
            // 差什么** —— 实测反馈：「为什么这里注册按钮是灰的？必须是正确的电话号码吗？
            // 这里需要给一个提示」。现在按钮常亮可点，由 `onSubmit()` 逐项校验并给出
            // 可读原因（昵称 / 手机号 / 密码三段，各自有明确文案）。
            Button(this.busy ? '处理中…'
              : (this.mode === 'login' ? '登录  →' : '注册并进入  →'))
              .width('100%').height(50).fontSize(16).fontWeight(FontWeight.Bold)
              .fontColor(UiTheme.surface)
              .backgroundColor(this.busy ? '#93C5FD' : UiTheme.accent)
              .borderRadius(8)
              .enabled(!this.busy)
              .onClick(() => { this.onSubmit(); })
          }
          .width('100%').padding({ top: 22 })

          // ---------- 提示 ----------
          if (this.message.length > 0) {
            Text(this.message)
              .fontSize(13).lineHeight(20).width('100%').padding(12).borderRadius(8)
              .fontColor(this.messageIsError ? '#EF4444' : UiTheme.secondary)
              .backgroundColor(this.messageIsError ? '#FEF2F2' : UiTheme.subtle)
              .margin({ top: 14 })
          }

          // ---------- 分隔 ----------
          Row({ space: 12 }) {
            Divider().color(UiTheme.line).layoutWeight(1)
            Text('或').fontSize(12).fontColor(UiTheme.muted)
            Divider().color(UiTheme.line).layoutWeight(1)
          }.width('100%').margin({ top: 28, bottom: 18 })

          Button(this.isHuaweiLoggingIn ? '正在进行华为账号登录…' : '使用华为账号登录', { type: ButtonType.Normal })
            .width('100%').constraintSize({ minHeight: 48 }).fontSize(15).fontWeight(FontWeight.Medium)
            .fontColor(UiTheme.ink).backgroundColor(UiTheme.surface)
            .borderRadius(8).border({ width: 1, color: UiTheme.line })
            .enabled(!this.busy)
            .onClick(() => { this.onHuaweiLogin(); })

          if (this.huaweiMessage.length > 0 || this.huaweiSetupHint.length > 0) {
            Column({ space: 6 }) {
              Text(this.huaweiMessage.length > 0 ? this.huaweiMessage : this.huaweiSetupHint)
                .fontSize(14).lineHeight(21).width('100%')
                .fontColor(this.huaweiMessage.length > 0 ? '#B91C1C' : UiTheme.secondary)
              if (ApiDefaults.ALLOW_DEBUG) {
                Button('查看连接设置', { type: ButtonType.Normal })
                  .width('100%').constraintSize({ minHeight: UiTheme.touch }).fontSize(14)
                  .fontColor(UiTheme.accent).backgroundColor(UiTheme.surface).borderRadius(8)
                  .enabled(!this.busy).onClick(() => {
                    this.huaweiMessage = '';
                    router.pushUrl({ url: 'pages/ApiEnvironment' });
                  })
              }
            }.width('100%').padding(12).backgroundColor(UiTheme.subtle).borderRadius(8).margin({ top: 8 })
          }

          if (ApiDefaults.ALLOW_DEMO) {
            Column({ space: 8 }) {
              Text('免登录体验').fontSize(13).fontWeight(FontWeight.Medium)
                .fontColor(UiTheme.secondary).width('100%')
              Button('跳过登录，在线体验', { type: ButtonType.Normal })
                .width('100%').constraintSize({ minHeight: UiTheme.touch }).fontSize(14)
                .fontColor(UiTheme.accent).backgroundColor(UiTheme.tint).borderRadius(8)
                .enabled(!this.busy).onClick(() => this.enterAsGuest())
              Button('离线演示（无需网络）', { type: ButtonType.Normal })
                .width('100%').constraintSize({ minHeight: UiTheme.touch }).fontSize(14)
                .fontColor(UiTheme.secondary).backgroundColor(UiTheme.surface)
                .border({ width: 1, color: UiTheme.line }).borderRadius(8)
                .enabled(!this.busy).onClick(() => this.enterOffline())
            }.width('100%').margin({ top: 24 }).padding({ top: 16 })
              .border({ width: { top: 1 }, color: UiTheme.line })
          }

          Blank().height(28)

          //  原文案写「不含手机号等敏感信息」，就印在**必填手机号 + 密码**的
          // 表单正下方 —— 与实现相反。如实改写。
          Text('手机号仅用于登录与账号识别；密码由服务端加盐哈希保存，不存明文。')
            .fontSize(11).fontColor('#B0B7C3').lineHeight(17).width('100%')

          Blank().height(24)
        }
        .width('100%')
        .constraintSize({ maxWidth: UiTheme.formWidth })
        .padding({ left: 24, right: 24 })
      }
      .layoutWeight(1)
      .width('100%')
      .scrollBar(BarState.Off)
      .align(Alignment.Top)
    }
    .width('100%').height('100%').backgroundColor(UiTheme.surface)

  }

  /**
   * 「登录 | 注册」分段。
   *
   *  **刻意不给本 Builder 传参**，而是直接读 `this.mode`。
   *
   * 原因：ArkUI 的 `@Builder` 对**参数**采用值传递，参数不是可观察依赖，
   * 外层状态变化时不保证重建。此前把 `this.mode` 当参数传进去，
   * 存在"点了注册但高亮/联动文案不刷新"的风险（本次一并消除）。
   * **无参 Builder 直接读 `this` 上的状态变量则依赖关系明确、必然刷新。**
   */
  @Builder
  ModeSegment() {
    Row({ space: 0 }) {
      ForEach(MODE_OPTIONS, (item: SegOption) => {
        Row({ space: 6 }) {
          Text(item.icon).fontSize(13)
          Text(item.label).fontSize(14)
            .fontWeight(this.mode === item.key ? FontWeight.Bold : FontWeight.Normal)
            .fontColor(this.mode === item.key ? UiTheme.accent : UiTheme.secondary)
        }
        .layoutWeight(1).height(44)
        .justifyContent(FlexAlign.Center)
        .backgroundColor(this.mode === item.key ? UiTheme.surface : UiTheme.subtle)
        .borderRadius(8)
        .onClick(() => {
          if (this.busy || this.isHuaweiLoggingIn || this.mode === item.key) return;
          if (item.key === 'register') {
            this.phone = '';
            this.nickname = '';
            this.password = '';
            this.grade = '';
            this.verifyCode = '';
            this.devCode = '';
            this.smsDelivered = false;
            this.stopResendTimer();
          }
          this.mode = item.key;
          this.message = '';
          this.messageIsError = false;
        })
      }, (item: SegOption) => item.key)
    }
    .width('100%').padding(4).backgroundColor(UiTheme.subtle).borderRadius(8)
    .margin({ bottom: 12 })
  }

  /** 「密码登录 | 验证码登录」分段。同样无参，直接读 `this.method`。 */
  @Builder
  MethodSegment() {
    Row({ space: 0 }) {
      ForEach(METHOD_OPTIONS, (item: SegOption) => {
        Row({ space: 6 }) {
          Text(item.icon).fontSize(13)
          Text(item.label).fontSize(14)
            .fontWeight(this.method === item.key ? FontWeight.Bold : FontWeight.Normal)
            .fontColor(this.method === item.key ? UiTheme.accent : UiTheme.secondary)
        }
        .layoutWeight(1).height(44)
        .justifyContent(FlexAlign.Center)
        .backgroundColor(this.method === item.key ? UiTheme.surface : UiTheme.subtle)
        .borderRadius(8)
        .onClick(() => {
          this.method = item.key;
          this.message = '';
          this.messageIsError = false;
        })
      }, (item: SegOption) => item.key)
    }
    .width('100%').padding(4).backgroundColor(UiTheme.subtle).borderRadius(8)
    .margin({ bottom: 12 })
  }

  /**
   * @deprecated 保留仅为兼容；新代码请用 `ModeSegment()` / `MethodSegment()`。
   * 传参版 Builder 存在状态不刷新的风险，故不再从 `build()` 调用。
   */
  @Builder
  Segmented(options: SegOption[], activeKey: string, onPick: (key: string) => void) {
    Row({ space: 0 }) {
      ForEach(options, (item: SegOption) => {
        Row({ space: 6 }) {
          Text(item.icon).fontSize(13)
          Text(item.label).fontSize(14)
            .fontWeight(activeKey === item.key ? FontWeight.Bold : FontWeight.Normal)
            .fontColor(activeKey === item.key ? UiTheme.accent : UiTheme.secondary)
        }
        .layoutWeight(1).height(44)
        .justifyContent(FlexAlign.Center)
        .backgroundColor(activeKey === item.key ? UiTheme.surface : UiTheme.subtle)
        .borderRadius(8)
        .onClick(() => onPick(item.key))
      }, (item: SegOption) => item.key)
    }
    .width('100%').padding(4).backgroundColor(UiTheme.subtle).borderRadius(8)
    .margin({ bottom: 12 })
  }
}

/** 分段选项常量（提到组件外，避免每次 build 重建数组）。 */
const MODE_OPTIONS: SegOption[] = [
  { key: 'login', label: '登录', icon: '' },
  { key: 'register', label: '注册', icon: '' }
];

const METHOD_OPTIONS: SegOption[] = [
  { key: 'password', label: '密码登录', icon: '' },
  { key: 'code', label: '验证码登录', icon: '' }
];

/** 分段控件的选项类型。 */
interface SegOption {
  key: string;
  label: string;
  icon: string;
}
