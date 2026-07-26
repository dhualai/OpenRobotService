# DB 测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## MySQLChecker

### DB-TC-001 — test_assert_row_exists_found

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 行存在返回数据

**测试点：** 验证行存在时返回数据

**前置条件：** MySQLClient 已连接；表中有数据

**测试步骤：**
1. 调用 checker.assert_row_exists(table, where) → 返回行数据

**结果：** PASS

---

## MySQLChecker

### DB-TC-002 — test_assert_row_exists_not_found

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 行不存在断言失败

**测试点：** 验证行不存在时断言失败

**前置条件：** MySQLClient 已连接；表中无匹配数据

**测试步骤：**
1. 调用 checker.assert_row_exists(table, where) → 断言失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-003 — test_assert_row_not_exists

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 行确实不存在

**测试点：** 验证行确实不存在时通过

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_not_exists(table, where) → 通过

**结果：** PASS

---

## MySQLChecker

### DB-TC-004 — test_assert_row_not_exists_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 行意外存在断言失败

**测试点：** 验证行意外存在时断言失败

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_not_exists(table, where) → 断言失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-005 — test_assert_row_count_exact_pass

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 精确行数匹配

**测试点：** 验证精确行数匹配时通过

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_count(table, expected=N) → 通过

**结果：** PASS

---

## MySQLChecker

### DB-TC-006 — test_assert_row_count_exact_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 行数不匹配断言失败

**测试点：** 验证行数不匹配时断言失败

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_count(table, expected=N) → 断言失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-007 — test_assert_row_count_min

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 最小行数约束

**测试点：** 验证最小行数约束

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_count(table, min=N) → 通过/失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-008 — test_assert_row_count_max

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 最大行数约束

**测试点：** 验证最大行数约束

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_row_count(table, max=N) → 通过/失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-009 — test_assert_matches

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 字段子集匹配

**测试点：** 验证字段子集匹配

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_matches(table, where, expected) → 通过/失败

**结果：** PASS

---

## MySQLChecker

### DB-TC-010 — test_assert_column_values

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 列枚举值正确

**测试点：** 验证列值在预期枚举内

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_column_values(table, column, expected_values) → 通过

**结果：** PASS

---

## MySQLChecker

### DB-TC-011 — test_assert_column_values_unexpected

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 列值不在预期内

**测试点：** 验证列值不在预期内时断言失败

**前置条件：** MySQLClient 已连接

**测试步骤：**
1. 调用 checker.assert_column_values(table, column, expected_values) → 断言失败

**结果：** PASS

---

## RedisChecker

### DB-TC-012 — test_assert_key_exists

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 键存在返回值

**测试点：** 验证键存在时返回值

**前置条件：** RedisClient 已连接；键存在

**测试步骤：**
1. 调用 checker.assert_key_exists(key) → 返回值

**结果：** PASS

---

## RedisChecker

### DB-TC-013 — test_assert_key_not_exists

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 键确实不存在

**测试点：** 验证键不存在时通过

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_key_not_exists(key) → 通过

**结果：** PASS

---

## RedisChecker

### DB-TC-014 — test_assert_key_not_exists_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 键意外存在断言失败

**测试点：** 验证键意外存在时断言失败

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_key_not_exists(key) → 断言失败

**结果：** PASS

---

## RedisChecker

### DB-TC-015 — test_assert_value_equals_pass

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 值精确匹配

**测试点：** 验证值精确匹配

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_value_equals(key, expected) → 通过

**结果：** PASS

---

## RedisChecker

### DB-TC-016 — test_assert_value_equals_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 值不匹配断言失败

**测试点：** 验证值不匹配时断言失败

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_value_equals(key, expected) → 断言失败

**结果：** PASS

---

## RedisChecker

### DB-TC-017 — test_assert_value_contains_pass

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 值包含子串

**测试点：** 验证值包含子串

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_value_contains(key, substring) → 通过

**结果：** PASS

---

## RedisChecker

### DB-TC-018 — test_assert_value_contains_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 值不包含子串断言失败

**测试点：** 验证值不包含子串时断言失败

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_value_contains(key, substring) → 断言失败

**结果：** PASS

---

## RedisChecker

### DB-TC-019 — test_assert_key_not_exists_on_missing

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 不存在键取值断言失败

**测试点：** 验证不存在键取值时断言失败

**前置条件：** RedisClient 已连接

**测试步骤：**
1. 调用 checker.assert_key_exists(missing_key) → 断言失败

**结果：** PASS

---

## QdrantChecker

### DB-TC-020 — test_assert_collection_exists

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 集合存在

**测试点：** 验证集合存在时通过

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_collection_exists(name) → 通过

**结果：** PASS

---

## QdrantChecker

### DB-TC-021 — test_assert_collection_not_exists

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 集合不存在

**测试点：** 验证集合不存在时通过

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_collection_not_exists(name) → 通过

**结果：** PASS

---

## QdrantChecker

### DB-TC-022 — test_assert_collection_not_exists_fail

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 集合意外存在断言失败

**测试点：** 验证集合意外存在时断言失败

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_collection_not_exists(name) → 断言失败

**结果：** PASS

---

## QdrantChecker

### DB-TC-023 — test_assert_search_returns

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 搜索结果含指定ID

**测试点：** 验证搜索结果包含指定ID

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_search_returns(collection, query, point_id) → 通过

**结果：** PASS

---

## QdrantChecker

### DB-TC-024 — test_assert_search_returns_missing

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 缺少指定ID断言失败

**测试点：** 验证缺少指定ID时断言失败

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_search_returns(collection, query, point_id) → 断言失败

**结果：** PASS

---

## QdrantChecker

### DB-TC-025 — test_assert_point_count

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 精确点数匹配

**测试点：** 验证集合内精确点数

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_point_count(collection, expected) → 通过

**结果：** PASS

---

## QdrantChecker

### DB-TC-026 — test_assert_point_count_wrong

**属性：** 优先级 P1 · 自动化 · 冒烟 是 · 功能点 点数不匹配断言失败

**测试点：** 验证点数不匹配时断言失败

**前置条件：** QdrantClient 已连接

**测试步骤：**
1. 调用 checker.assert_point_count(collection, expected) → 断言失败

**结果：** PASS

---
