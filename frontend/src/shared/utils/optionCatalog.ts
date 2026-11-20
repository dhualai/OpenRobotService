// 详情模板页「下拉选项」输入框旁边那个一键填充按钮的目录表。
//
// **不是所有下拉都是车型目录**（「项目类型」「载具类型」也是下拉），所以按钮文案
// 不能写死成「填入车型目录」：这里按**节点自身特征**（标题 + 现有选项）挑目录，
// 匹配不到目录的下拉（如项目类型）就不出按钮，选项由管理员自己敲。
// 以后要多一份常用清单（例如项目类型），往 OPTION_CATALOGS 里加一条即可。

import { isKnownVehicleModel, VEHICLE_MODEL_CODES, VEHICLE_MODEL_TOTAL } from './vehicleModels';

export interface OptionCatalog {
  /** 按钮文字，如「填入车型目录」 */
  label: string;
  /** 按钮悬停说明 */
  hint: string;
  /** 一次性铺进下拉选项的清单（保序） */
  values: string[];
}

/** 判定目录只看节点标题与现有选项，够用且不必牵扯整棵树 */
export interface CatalogTargetNode {
  title: string;
  options?: string[];
}

/** 「车型1 / 车型2 / 车型」这类节点的标题形态 */
const VEHICLE_MODEL_TITLE = /^车型\s*\d*$/;

/** 是不是车型下拉：标题叫「车型N」，或选项里已经装着车型型号（改过名的也认得出来） */
function isVehicleModelDropdown(node: CatalogTargetNode): boolean {
  if (VEHICLE_MODEL_TITLE.test((node.title ?? '').trim())) return true;
  return (node.options ?? []).some((option) => isKnownVehicleModel(option));
}

const VEHICLE_MODEL_CATALOG: OptionCatalog = {
  label: '填入车型目录',
  hint: `填入 ${VEHICLE_MODEL_TOTAL} 款 AGV 车型型号`,
  values: VEHICLE_MODEL_CODES,
};

/** 目录表：按节点特征从上往下匹配，命中即用 */
const OPTION_CATALOGS: { match: (node: CatalogTargetNode) => boolean; catalog: OptionCatalog }[] = [
  { match: isVehicleModelDropdown, catalog: VEHICLE_MODEL_CATALOG },
];

/** 这个下拉有没有现成目录可一键填；没有（如「项目类型」）返回 null，页面不出按钮 */
export function optionCatalogFor(node: CatalogTargetNode): OptionCatalog | null {
  return OPTION_CATALOGS.find((entry) => entry.match(node))?.catalog ?? null;
}
