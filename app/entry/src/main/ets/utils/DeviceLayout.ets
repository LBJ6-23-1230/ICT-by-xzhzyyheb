import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';

const DOMAIN: number = 0x0001;
const TAG: string = 'DeviceLayout';

/** Reports the main window in vp for layouts that depend on available space. */
export class DeviceLayout {
  /** 断点档位：手机 / 平板 / 桌面（含 2in1）。 */
  static readonly PHONE: string = 'phone';
  static readonly TABLET: string = 'tablet';
  static readonly DESKTOP: string = 'desktop';

  /** 平板起始宽度（vp）。对齐 HarmonyOS 常见断点（sm < 600 ≤ md < 840 ≤ lg）。 */
  static readonly TABLET_MIN_WIDTH: number = 600;
  /** 桌面起始宽度（vp）。 */
  static readonly DESKTOP_MIN_WIDTH: number = 840;

  /**
   * 正文区最大宽度（vp）。
   *
   * 大屏上把内容限制在这个宽度内并居中，避免一行文本过长导致阅读困难。
   * 取值参考常见阅读舒适区（约 700–800vp）。
   */
  static readonly CONTENT_MAX_WIDTH: number = 720;

  private static widthVp: number = 0;
  private static breakpoint: string = DeviceLayout.PHONE;
  private static mainWindow: window.Window | null = null;
  private static sizeListener: ((size: window.Size) => void) | null = null;

  /** 当前窗口宽度（vp）；未初始化时为 0。 */
  static windowWidth(): number {
    return DeviceLayout.widthVp;
  }

  /** 当前断点档位（`phone` / `tablet` / `desktop`）。 */
  static current(): string {
    return DeviceLayout.breakpoint;
  }

  /** 是否大屏（平板或桌面）。 */
  static isLargeScreen(): boolean {
    return DeviceLayout.breakpoint !== DeviceLayout.PHONE;
  }

  /**
   * 正文区应使用的最大宽度（vp）。
   *
   * 手机上返回一个足够大的值，等价于"不约束" ——
   * 注意**不能返回 `Infinity`**：ArkUI 的 `constraintSize` 接受 `Length`，
   * 而 `Infinity` 不是合法的 `Length`，会导致布局异常。
   */
  static contentMaxWidth(): number {
    return DeviceLayout.isLargeScreen() ? DeviceLayout.CONTENT_MAX_WIDTH : 10000;
  }

  /**
   * 绑定主窗口并在尺寸变化时更新断点。
   *
   * 在 `EntryAbility.onWindowStageCreate` 里调用一次即可。
   * 失败不抛异常 —— 布局判断不该阻断启动。
   */
  static attach(windowStage: window.WindowStage): void {
    DeviceLayout.detach();
    try {
      const mainWindow: window.Window = windowStage.getMainWindowSync();
      const uiContext = mainWindow.getUIContext();
      const listener = (size: window.Size): void => {
        DeviceLayout.update(uiContext.px2vp(size.width));
        AppStorage.setOrCreate('layoutHeightVp', uiContext.px2vp(size.height));
      };
      const rect = mainWindow.getWindowProperties().windowRect;
      listener({ width: rect.width, height: rect.height });
      mainWindow.on('windowSizeChange', listener);
      DeviceLayout.mainWindow = mainWindow;
      DeviceLayout.sizeListener = listener;
    } catch (error) {
      const typed = error as BusinessErrorLike;
      hilog.warn(DOMAIN, TAG, '%{public}s',
        `DeviceLayout.attach failed: ${typed.code} ${typed.message}`);
    }
  }

  static detach(): void {
    if (DeviceLayout.mainWindow !== null && DeviceLayout.sizeListener !== null) {
      try {
        DeviceLayout.mainWindow.off('windowSizeChange', DeviceLayout.sizeListener);
      } catch (error) {
        const typed = error as BusinessErrorLike;
        hilog.warn(DOMAIN, TAG, 'DeviceLayout.detach failed: %{public}d', typed.code);
      }
    }
    DeviceLayout.mainWindow = null;
    DeviceLayout.sizeListener = null;
  }

  /** 按窗口宽度（vp）重新判定档位。 */
  static update(widthVp: number): void {
    if (!(widthVp > 0)) {
      return;
    }
    DeviceLayout.widthVp = widthVp;
    DeviceLayout.breakpoint = DeviceLayout.classify(widthVp);
  }

  /** 供测试与调试：把断点判定逻辑单独暴露（纯函数，不依赖窗口）。 */
  static classify(widthVp: number): string {
    if (!(widthVp > 0)) {
      return DeviceLayout.PHONE;
    }
    if (widthVp >= DeviceLayout.DESKTOP_MIN_WIDTH) {
      return DeviceLayout.DESKTOP;
    }
    if (widthVp >= DeviceLayout.TABLET_MIN_WIDTH) {
      return DeviceLayout.TABLET;
    }
    return DeviceLayout.PHONE;
  }

  /** 人类可读标签，供「接口环境」页之类的地方显示当前形态。 */
  static describe(): string {
    const label: string = DeviceLayout.breakpoint === DeviceLayout.DESKTOP ? '桌面 / 2in1'
      : (DeviceLayout.breakpoint === DeviceLayout.TABLET ? '平板' : '手机');
    return `${label}（${Math.round(DeviceLayout.widthVp)}vp）`;
  }
}

interface BusinessErrorLike {
  code: number;
  message: string;
}
