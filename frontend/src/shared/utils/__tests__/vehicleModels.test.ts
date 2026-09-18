import { describe, it, expect } from 'vitest';
import {
  isKnownVehicleModel,
  VEHICLE_MODEL_CODES,
  VEHICLE_MODEL_TOTAL,
} from '../vehicleModels';

describe('vehicleModels（AGV 车型目录）', () => {
  it('66 款，型号不重复，顺序即下拉选项顺序', () => {
    expect(VEHICLE_MODEL_CODES).toHaveLength(66);
    expect(new Set(VEHICLE_MODEL_CODES).size).toBe(66);
    expect(VEHICLE_MODEL_TOTAL).toBe(66);
    // 首尾与用户给定的清单一致（顺序敏感：铺进下拉就是这个顺序）
    expect(VEHICLE_MODEL_CODES[0]).toBe('UHX-01');
    expect(VEHICLE_MODEL_CODES[65]).toBe('XORD3');
    // 抽查：每段新增/保留的都铺得到
    for (const code of ['XFC001', 'XCD202Y', 'XFL151E', 'XS1201', 'XC1051', 'XCB031']) {
      expect(VEHICLE_MODEL_CODES).toContain(code);
    }
    // 改名款：XS1161 → XS1201，旧型号不再出现在目录里
    expect(VEHICLE_MODEL_CODES).not.toContain('XS1161');
  });

  it('isKnownVehicleModel 只认目录里的型号（去空白）', () => {
    expect(isKnownVehicleModel('XC1051')).toBe(true);
    expect(isKnownVehicleModel(' XCD301 ')).toBe(true);
    expect(isKnownVehicleModel('XORD1')).toBe(true);
    expect(isKnownVehicleModel('车型1')).toBe(false);   // 模板占位的节点名不是型号
    expect(isKnownVehicleModel('XS1161')).toBe(false);  // 改名后的旧型号
    expect(isKnownVehicleModel('XYZ-100')).toBe(false); // 目录外的自编型号不算
    expect(isKnownVehicleModel('')).toBe(false);
  });
});
