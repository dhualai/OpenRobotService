// 项目生命周期阶段 → 项目时间进度的共享定义。
// 项目详情页（ProjectDetail）与项目进度列表卡（ProjectProgress）共用，
// 保证同一项目在两处展示的「项目时间进度」百分比口径完全一致。
// 与 backend ProjectStatus 枚举严格一致（顺序即生命周期顺序），"项目中止"为终止分支单独处理。

export const STATUS_OPTIONS = [
  '售前方案', '签单洽谈', '已签合同', '出厂测试', '即将进场', '延期进场',
  '正在实施', '实施暂停', '实施运行', '试运行中', '验收运营', '项目中止', '项目结束',
];

export const PROJECT_ABORTED = '项目中止';

/** 生命周期阶段：全部状态去掉「项目中止」（终止分支不参与进度线性推进） */
export const LIFECYCLE_STATUSES = STATUS_OPTIONS.filter((s) => s !== PROJECT_ABORTED);

/** 项目时间进度（0-100）：按生命周期阶段序号线性均分，与项目详情页口径一致。
 *  状态缺失或不在阶段列表时返回 0（非阻塞，不展示伪造进度）。 */
export function calcLifecycleProgress(status?: string | null): number {
  if (!status) return 0;
  const index = LIFECYCLE_STATUSES.indexOf(status);
  if (index < 0) return 0;
  return Math.round((index / (LIFECYCLE_STATUSES.length - 1)) * 100);
}
