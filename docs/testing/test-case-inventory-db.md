# DB 测试用例清单

> 3 个 Checker，26 个测试用例。详情见 [db-test-plan.md](db-test-plan.md)。

## MySQLChecker（11 用例）

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 1 | test_assert_row_exists_found | 行存在返回数据 |
| 2 | test_assert_row_exists_not_found | 行不存在→断言失败 |
| 3 | test_assert_row_not_exists | 行确实不存在 |
| 4 | test_assert_row_not_exists_fail | 行意外存在→断言失败 |
| 5 | test_assert_row_count_exact_pass | 精确行数匹配 |
| 6 | test_assert_row_count_exact_fail | 行数不匹配→断言失败 |
| 7 | test_assert_row_count_min | 最小行数约束 |
| 8 | test_assert_row_count_max | 最大行数约束 |
| 9 | test_assert_matches | 字段子集匹配 |
| 10 | test_assert_column_values | 列枚举值正确 |
| 11 | test_assert_column_values_unexpected | 列值不在预期内 |

## RedisChecker（8 用例）

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 12 | test_assert_key_exists | 键存在返回值 |
| 13 | test_assert_key_not_exists | 键确实不存在 |
| 14 | test_assert_key_not_exists_fail | 键意外存在→断言失败 |
| 15 | test_assert_value_equals_pass | 值精确匹配 |
| 16 | test_assert_value_equals_fail | 值不匹配→断言失败 |
| 17 | test_assert_value_contains_pass | 值包含子串 |
| 18 | test_assert_value_contains_fail | 值不包含子串→断言失败 |
| 19 | test_assert_key_not_exists_on_missing | 不存在的键取值→断言失败 |

## QdrantChecker（7 用例）

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 20 | test_assert_collection_exists | 集合存在 |
| 21 | test_assert_collection_not_exists | 集合确实不存在 |
| 22 | test_assert_collection_not_exists_fail | 集合意外存在→断言失败 |
| 23 | test_assert_search_returns | 搜索结果含指定 ID |
| 24 | test_assert_search_returns_missing | 缺少指定 ID→断言失败 |
| 25 | test_assert_point_count | 精确点数匹配 |
| 26 | test_assert_point_count_wrong | 点数不匹配→断言失败 |

**合计：26 用例 ✅**
