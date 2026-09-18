// AGV 车型目录（66 款）——「硬件 / 车辆」下车型1/车型2… 的下拉选项来源。
//
// 车型是**值**不是节点名：车型1/车型2 是全局节点（各项目共用同一定义），
// 所以型号只能写进各项目自己的值里，写成节点名会撞上「全局字段定义只能在详情模板改」。
// 模板页的「填入车型目录」按钮用 VEHICLE_MODEL_CODES 一次性铺满选项，
// 文件导入页用 isKnownVehicleModel 认出型号并落成下拉值。
//
// 型号清单与后端 backend/app/modules/admin/services/info_node_import_service.py 的
// VEHICLE_MODEL_CODES **必须一字不差**（文件导入的 AI 提示词用同一份清单规范车型写法）；
// 中文全称只后端有（只进提示词，前端不展示），改型号时两边一起动。
// 库里车型1/车型2 的 config.options 由迁移 4a7c2e9d1b53 同步，别只改代码。

/** 车型型号清单：数组顺序 = 下拉选项顺序（2026-09-18 用户给定） */
export const VEHICLE_MODEL_CODES: string[] = [
  'UHX-01', 'EXP15', 'RPG201', 'XCART', 'XC1031', 'XC1051', 'XC1061', 'XCS101U',
  'XCU0051', 'XCL0051', 'XCO0051', 'XCB031', 'XCF101', 'XFC001', 'XFC002', 'XCD0051',
  'XCD031', 'XCD061', 'XCD062', 'XCD101', 'XCD151', 'XCD202Y', 'XCD301', 'XCD501',
  'XCT201', 'XTD401', 'XTD601', 'XPA152', 'XPC151', 'XPG151', 'XP1151', 'XP1152',
  'XP1153', 'XP1201', 'XP3201', 'XPL201', 'XPL201P', 'XPL201T', 'XPL301', 'XPL501',
  'XQE122', 'XQE151', 'XQC161', 'XQC163', 'XQC201', 'XQS151', 'XQS181', 'XS1151',
  'XS1152', 'XS1201', 'XS2201', 'XSC081', 'XSC121', 'XSC151', 'XSC201', 'XSF101',
  'XSG121', 'XNA101', 'XNA121', 'XNA151', 'XFL151E', 'XFL201', 'XFL301', 'XFL351',
  'XORD1', 'XORD3',
];

/** 目录款数（模板页「填入 N 款 AGV 车型型号」的 N） */
export const VEHICLE_MODEL_TOTAL = VEHICLE_MODEL_CODES.length;

const MODEL_CODES = new Set(VEHICLE_MODEL_CODES);

/** 标题/文本是否就是目录里的车型型号（识别导入内容、回显选中的型号） */
export function isKnownVehicleModel(title: string): boolean {
  return MODEL_CODES.has((title ?? '').trim());
}
