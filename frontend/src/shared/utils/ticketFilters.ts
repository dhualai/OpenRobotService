// 系统任务「相关性分类」过滤条件的共享定义：
// 工单列表查询、分类角标计数（TasksView）与底部导航「待我处理」角标（MainLayout）共用，
// 保证各处口径一致。与后端 TicketFilter 复合过滤结构对应（支持 or/and 嵌套）。
export interface TicketFilterCondition {
  field?: string;
  op?: string;
  value?: string;
  or?: TicketFilterCondition[];
  and?: TicketFilterCondition[];
}

// 相关性分类（全部/项目相关/待我处理/与我相关）的基础过滤条件，不含搜索/状态/优先级。
// 待我处理/与我相关在缺少用户名时回退为项目维度，与列表行为一致。
export const buildRelevanceFilters = (
  relevance: string,
  username: string,
  projectIds: string[],
): TicketFilterCondition[] => {
  if (relevance === 'global') {
    // 「全部」：不过滤项目、人员相关性，直接拉全量
    return [];
  }
  if (relevance === 'mine' && username) {
    const workingStatusFilters = [
      { field: 'status', op: 'eq', value: 'new' },
      { field: 'status', op: 'eq', value: 'in_progress' },
      { field: 'status', op: 'eq', value: 'pending' },
    ];
    return [{
      or: [
        {
          and: [
            { or: workingStatusFilters },
            { field: 'assignedTo', op: 'eq', value: username },
          ],
        },
        {
          and: [
            { field: 'status', op: 'eq', value: 'resolved' },
            { field: 'createdBy', op: 'eq', value: username },
          ],
        },
      ],
    }];
  }
  if (relevance === 'related' && username) {
    const userRelatedFilters = [
      { field: 'createdBy', op: 'eq', value: username },
      { field: 'createdByName', op: 'contains', value: username },
      { field: 'assignedTo', op: 'eq', value: username },
      { field: 'assignedToName', op: 'contains', value: username },
      { field: 'customer', op: 'eq', value: username },
      { field: 'customerName', op: 'contains', value: username },
    ];
    return [{ or: userRelatedFilters }];
  }
  // 「项目相关」：仅展示与当前用户关联的项目（projectIds）下的工单，
  // 项目列表为空时（未加载/无项目）回退为不限制。
  return projectIds.length > 0
    ? [{ or: projectIds.map((pid) => ({ field: 'projectId', op: 'eq', value: pid })) }]
    : [];
};
