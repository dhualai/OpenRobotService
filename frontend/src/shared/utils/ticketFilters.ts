// 系统任务「相关性分类」过滤条件的共享定义：
// 工单列表查询、分类角标计数（TasksView）与底部导航「待我处理」角标（MainLayout）共用，
// 保证各处口径一致。与后端 TicketFilter 复合过滤结构对应（支持 or/and 嵌套）。
export interface TicketFilterCondition {
  field?: string;
  op?: string;
  value?: string | number | boolean | (string | number | boolean)[];
  or?: TicketFilterCondition[];
  and?: TicketFilterCondition[];
}

// 相关性分类（全部/项目相关/待我处理/与我相关/我关注的）的基础过滤条件，不含搜索/状态/优先级。
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
            { field: 'assignedTo', op: 'eq', value: username },  // username 或 users.id，后端双键解析
          ],
        },
        {
          and: [
            { field: 'status', op: 'eq', value: 'resolved' },
            { field: 'createdBy', op: 'eq', value: username },
          ],
        },
        // 回合协商：接单人刚改过 step 且尚未协商一致，轮到提单人确认/答复
        {
          and: [
            { or: workingStatusFilters },
            { field: 'createdBy', op: 'eq', value: username },
            { field: 'stepUpdatedBy', op: 'eq', value: 'assigned' },
            { field: 'currStepAgreed', op: 'eq', value: false },
          ],
        },
      ],
    }];
  }
  if (relevance === 'followed' && username) {
    // 「我关注的」：命中当前用户在 task_followers 表中的工单。
    // value 仅为占位，后端忽略前端 value、统一用 token 解析的当前用户，杜绝越权。
    // 无用户名时返回空条件（后端同样以 current_username 兜底，未登录请求 401，不会泄漏全量）。
    return [{ field: 'followedBy', op: 'eq', value: true }];
  }
  if (relevance === 'related' && username) {
    const userRelatedFilters = [
      { field: 'createdBy', op: 'eq', value: username },
      { field: 'createdByName', op: 'contains', value: username },
      { field: 'assignedTo', op: 'eq', value: username },
      { field: 'assignedToName', op: 'contains', value: username },
      { field: 'customer', op: 'eq', value: username },
      { field: 'customerName', op: 'contains', value: username },
      // 我参与的工单：当前用户在 task_participants 表中（评论/附件等行为触发写入）
      { field: 'participatedBy', op: 'eq', value: true },
    ];
    return [{ or: userRelatedFilters }];
  }
  // 「项目相关」：仅展示与当前用户关联的项目（projectIds）下的工单，
  // 项目列表为空时（未加载/无项目）回退为不限制。
  return projectIds.length > 0
    ? [{ or: projectIds.map((pid) => ({ field: 'projectId', op: 'eq', value: pid })) }]
    : [];
};
