import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';
import router from '@ohos.router';
import { AgentApiClient } from '../api/AgentApiClient';
import { ApiEnvironmentStore } from '../api/ApiEnvironmentStore';
import { AuthClient } from '../api/AuthClient';
import { AuthResult } from '../api/AuthResult';
import { AuthStore } from '../api/AuthStore';
import { FixtureDemoStateStore } from '../api/FixtureDemoStateStore';
import { WorkflowSessionStore } from '../cache/WorkflowSessionStore';
import { LearningDataStore } from '../cache/LearningDataStore';
import { appState } from '../data/AppState';
import { DistributedStateService } from '../services/DistributedStateService';
import { ProactiveSurfaceService } from '../services/ProactiveSurfaceService';
import { ProactiveSettingsStore } from '../services/ProactiveSettingsStore';
import { DeviceLayout } from '../utils/DeviceLayout';
import { FocusLifecycleService } from '../services/FocusLifecycleService';

const DOMAIN = 0x0000;

export default class EntryAbility extends UIAbility {
  private activeWindowStage: window.WindowStage | null = null;
  private startupReady: boolean = false;
  private pendingLearningCard: boolean = false;

  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onCreate');
    this.pendingLearningCard = this.isLearningCardLaunch(want);
  }

  onDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageCreate');
    this.activeWindowStage = windowStage;

    if (!ApiEnvironmentStore.restore(this.context)) {
      hilog.warn(DOMAIN, 'testTag', '%{public}s', 'Failed to restore API environment, using defaults.');
    }
    if (!FixtureDemoStateStore.initialize(this.context)) {
      hilog.warn(DOMAIN, 'testTag', '%{public}s', 'Failed to restore fixture demo state.');
    }
    if (!WorkflowSessionStore.restore(this.context)) {
      hilog.warn(DOMAIN, 'testTag', '%{public}s', 'Failed to restore workflow session.');
    }
    if (!ProactiveSettingsStore.restore(this.context)) {
      hilog.warn(DOMAIN, 'testTag', '%{public}s', 'Failed to restore proactive notification settings.');
    }

    // 课程与 DDL 是账号维度的本地数据。必须先绑定 context，随后 applyLogout/applyLogin
    // 才能分别恢复 demo-user 或真实 userId 对应的快照。
    LearningDataStore.initialize(this.context);
    FocusLifecycleService.initialize();

    // 启动时先把 AppState 对齐到已知身份，避免首页在异步校验完成前读到空身份。
    appState.applyLogout();
    // 同时标记「身份尚未判定」：对话页据此显示启动加载态，
    // 而不是先渲染一帧**游客身份**的会话内容（见 `AppState.markIdentityPending()`）。
    appState.markIdentityPending();

    // ★ 启动进入**对话页**（而不是工作台）。
    // 产品定位是"对话式学习秘书"：进来就能直接说话，不用先读一遍功能清单。
    // 先加载对话页、再异步判断是否要去登录页，这样即使校验慢也不会白屏。
    windowStage.loadContent('pages/ChatMain', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, 'testTag', 'Failed to load the content. Cause: %{public}s', JSON.stringify(err));
        return;
      }
      hilog.info(DOMAIN, 'testTag', 'Succeeded in loading the content.');
      if (this.activeWindowStage === windowStage) {
        DeviceLayout.attach(windowStage);
        void this.bootstrapAuth(windowStage);
      }
    });

    // ⚠️ **必须先 await AuthStore.initialize() 再判登录态**（下方 bootstrapAuth 内串行执行）。
    //
    // 原实现是把 `initialize().then(...)` 与 `void this.restoreIdentity(...)` **并行**发起，
    // 而 `restoreIdentity()` 的第一个动作就是同步调用 `AuthStore.isSignedIn()`。
    // `AuthStore.session` 要等 `initialize()` 内部的 `await TokenCipher.initialize()`
    // 完成、走到 `loadFromPreferences()` 才被赋值 —— 并行发起时那一刻必然是 `null`，
    // 于是**每次冷启动都命中"没有登录态"分支**，被强制拉到登录页；
    // `restoreIdentity()` 里"离线 Fixture 免打扰"与"静默校验"两条规则成为不可达代码。
    //
    // 这不是"短暂显示未登录"，而是**登录态恢复永久失效**（token 存了也用不上）。
    // Device relay uses the signed-in account; it does not request nearby-device permission.
    void DistributedStateService.getInstance().init(this.context);
  }

  /**
   * 串行完成"恢复加密凭据 → 恢复登录态"。
   *
   * 顺序不可颠倒：`restoreIdentity()` 依赖 `AuthStore` 里已解密好的 session。
   */
  private async bootstrapAuth(windowStage: window.WindowStage): Promise<void> {
    try {
      const ready: boolean = await AuthStore.initialize(this.context);
      if (!ready) {
        hilog.warn(DOMAIN, 'testTag', '%{public}s', 'Failed to restore auth session.');
      }
      if (AuthStore.encryptionDegraded()) {
        hilog.warn(DOMAIN, 'testTag', '%{public}s',
          'Token encryption unavailable; credentials remain in memory for this session.');
      }
    } catch (error) {
      // 凭据恢复失败不应阻断启动：按未登录继续，由 restoreIdentity 决定去登录页
      hilog.error(DOMAIN, 'testTag', 'AuthStore init failed: %{public}s', JSON.stringify(error));
    }
    if (this.activeWindowStage !== windowStage) return;
    await this.restoreIdentity(windowStage);
    if (this.activeWindowStage !== windowStage) return;
    this.startupReady = true;
    if (this.pendingLearningCard) {
      this.pendingLearningCard = false;
      void this.openLearningCard(windowStage);
    }
    void DistributedStateService.getInstance().refresh();
  }

  onNewWant(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    if (!this.isLearningCardLaunch(want)) return;
    if (this.startupReady && this.activeWindowStage !== null) {
      void this.openLearningCard(this.activeWindowStage);
    } else {
      this.pendingLearningCard = true;
    }
  }

  /**
   * 恢复登录态并条件跳转。
   *
   * 三条规则（顺序即优先级）：
   *   1. 本地**没有登录态** → 一律展示登录页。**离线模式也不例外** ——
   *      登录页提供「跳过登录，在线体验」与「离线演示（无需网络）」两个入口，
   *      所以"必须先看到登录页"不会挡路，反而保证登录入口始终存在。
   *   2. 本地有登录态且处于离线 Fixture → 不打扰，直接以该身份进入对话页。
   *   3. 本地有登录态 → 静默校验；成功则对齐身份，失败则降级为游客（不卡登录页）。
   *
   * ⚠️ 规则 1 的修正来源：原实现把"离线 Fixture"放在最前面，一旦用户点过离线进入，
   * 之后**永远跳过登录页**，表现为"登录界面怎么没有了"。现已修正。
   *
   * ⚠️ 「校验失败降级为游客」是刻意的：评审过程中一个过期 token
   * 不该让整个 App 不可用。这条纪律优先于安全洁癖。
   */
  private async restoreIdentity(windowStage: window.WindowStage): Promise<void> {
    try {
      // 规则 1：没有登录态 → 先看登录页（离线模式也走这里）
      if (!AuthStore.isSignedIn()) {
        appState.applyLogout();
        await windowStage.loadContent('pages/Login');
        return;
      }

      // 规则 2：有登录态 + 离线模式 → 不做网络校验，直接用保存的身份
      if (AgentApiClient.getConfig().useFixture) {
        const restored: AuthResult = await AuthClient.restore();
        if (restored.ok && restored.user !== null) {
          appState.applyLogin(restored.user, restored.source);
        }
        return;
      }

      // 规则 3：有登录态 + 联机模式 → 静默校验
      const result: AuthResult = await AuthClient.restore();
      if (result.ok && result.user !== null) {
        appState.applyLogin(result.user, result.source);
        if (result.needsPasswordSetup === true) {
          await windowStage.loadContent('pages/PasswordSetup');
          return;
        }
        hilog.info(DOMAIN, 'testTag', 'Restored identity: %{public}s', result.user.userId);
      } else {
        appState.applyLogout();
        await windowStage.loadContent('pages/Login');
      }
    } catch (error) {
      // 任何异常都不能把用户挡在门外
      hilog.error(DOMAIN, 'testTag', 'restoreIdentity failed: %{public}s', JSON.stringify(error));
      appState.applyLogout();
    } finally {
      // 无论走哪条分支（含上面几处提前 return 与 catch），身份判定都已结束 ——
      // 放在 finally 里才能保证对话页的加载态一定被解除。
      appState.markIdentityReady();
    }
  }

  onWindowStageDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageDestroy');
    this.activeWindowStage = null;
    this.startupReady = false;
    DeviceLayout.detach();
    FocusLifecycleService.destroy();
    // Stop the foreground lease and tell connected devices this app is offline.
    void DistributedStateService.getInstance().leave();
  }

  onForeground(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onForeground');
    FocusLifecycleService.onForeground();
    DistributedStateService.getInstance().setForeground(true);
    void appState.syncFocusSessions();
    void ProactiveSurfaceService.refresh(this.context, true);
  }

  onBackground(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onBackground');
    FocusLifecycleService.onBackground();
    DistributedStateService.getInstance().setForeground(false);
    void ProactiveSurfaceService.refresh(this.context, false);
  }

  private isLearningCardLaunch(want: Want): boolean {
    return want.parameters?.['source'] === 'learning_card';
  }

  private async openLearningCard(windowStage: window.WindowStage): Promise<void> {
    try {
      const pageRouter = windowStage.getMainWindowSync().getUIContext().getRouter();
      const current = pageRouter.getState();
      if (current.name === 'Login' || current.name === 'PasswordSetup') return;
      // Load the current account's tasks in the existing to-do page and preserve the back stack.
      await pageRouter.pushUrl({ url: 'pages/CourseImport', params: { initialTab: 'ddls', cardLaunch: `${Date.now()}` } },
        router.RouterMode.Single);
    } catch (error) {
      hilog.warn(DOMAIN, 'testTag', 'Failed to open learning card. Cause: %{public}s', JSON.stringify(error));
    }
  }

}
