# USP产品开发和禅道功能AI优化方案

## 一、USP 产品开发主流程

```mermaid
flowchart TD
    request["项目经理：提出需求"] --> product["产品经理：整理需求并提交研发"]
    product --> review["研发：技术可行性评审"]
    review --> approved{"需求评审是否通过？"}
    approved -->|"不通过，给出理由"| revise["产品经理：调整需求"]
    revise --> review
    approved -->|"通过"| schedule["产品经理：排期、拆分模块任务<br/>选择研发人员与研发日期"]
    schedule --> accept["研发：接收任务或自行认领"]
    accept --> develop["H 研发：开发 → 上传服务器 → 自测"]
    develop --> scope["I 产品经理：确认范围并下发打 tag 工单"]
    scope --> tag["指定人员：执行打 tag 工单"]
    tag --> package["J 出包人员：打包"]
    package --> test["K 测试人员：下一周测试该版本"]
    test --> baseline["基线基础测试（AI）"]
    test --> manual["本周新功能人工测试"]
    baseline --> summary["测试人员：汇总测试结果"]
    manual --> summary
    summary --> found{"是否发现 Bug？"}
    found -->|"是"| bug["测试人员：下发 Bug 工单"]
    bug --> fix["责任研发：修复、自测<br/>需要新包时按出包流程交付"]
    fix --> regression["测试人员：回归测试"]
    regression --> passed{"回归是否通过？"}
    passed -->|"不通过"| bug
    passed -->|"通过"| result["测试人员：记录测试结论"]
    found -->|"否"| result
    result --> close["产品经理：确认交付结果与版本结项"]
```

# 二、需求整理、拆分与评审的 AI 优化

```mermaid
flowchart TB
    subgraph taskSplit["AI 拆分需求"]
        demand["产品需求＋历史需求/任务"] --> split["AI 拆分功能、任务和依赖"]
        modules["责任模块树：模块与负责人"] --> split
        split --> draft["拆分结果形成草稿<br/>给出对应模块及研发人员"]
        draft --> confirm["产品经理确认"]
        confirm --> dispatch["正式下发任务工单"]
    end
    subgraph meetingReview["AI 接入评审会议纪要"]
        minutes["work buddy／企微导入会议纪要"] --> extract["AI 提取每项需求的评审结论<br/>通过／不通过及理由"]
        extract --> update["同步对应需求和工单状态"]
    end
```

# 三、自测、基线测试与失败用例的 AI 优化

```mermaid
flowchart TB
    subgraph developerTest["研发自测阶段"]
        upload["研发上传服务器"] --> selftest["研发自测"]
        cases["已有基础测试用例"] --> earlier["前置到研发自测阶段执行"]
        selftest --> selfresult["自测结果"]
        earlier --> selfresult
    end
    subgraph testerTest["测试阶段"]
        build["本周交付版本"] --> aiTest["基线基础测试（AI）"]
        aiTest --> failures["失败测试用例<br/>保留步骤、结果和报告"]
        failures --> owner["对应功能模块及责任研发"]
        owner --> ticket["通过工单发送给相应人员"]
        ticket --> repair["研发修复 → 测试回归"]
        build --> human["本周新功能：人工测试"]
        human --> humanBug["测试人员直接新建 Bug 单"]
    end
```

第一版先支持测试人员在 USP 页面直接新建 Bug 单；AI 测试后把失败用例通过工单发送给责任人员，放入后续迭代。

## 四、每周更新与功能文档的 AI 优化

```mermaid
flowchart TB
    subgraph weeklyUpdate["每周研发更新同步功能拓扑"]
        weekly["研发人员每周更新的内容"] --> weeklyAI["AI 汇总对应版本和功能变化"]
        weeklyAI --> topology["自动同步到功能拓扑的对应页面"]
    end
    subgraph knowledgeDocument["需求与知识库联动"]
        requirement["需求、研发任务和功能内容"] --> knowledge["结合知识库 Agent"]
        knowledge --> document["AI 生成关联的功能文档"]
    end
```

## 五、会议中提出的工单自动化

```mermaid
flowchart TB
    productConfirm["产品经理确认范围"] --> tagTicket["下发打 tag 工单"]
    tagTicket --> autoTag["后续：工单绑定自动打 tag"]
    autoTag --> release["发布工单：打包 → 测试<br/>处理人员可配置"]
    change["评审结果／测试失败／工单状态变化"] --> notification["通过关联工单发送通知"]
    notification --> chat["108 聊天消息送达"]
```

自动打 tag 的顺序按本次补充执行：先由产品经理确认范围并下发工单，不在研发自测完成时直接触发。

## 六、AI 如何实现：两句话总结

AI 读取需求、会议纪要、责任模块树和测试结果，辅助拆分任务、提取纪要中的评审结论、分析失败用例，并生成每周更新和功能文档。需求拆分草稿由产品经理确认后下发，失败用例通过工单送达对应人员，研发周更新自动同步至功能拓扑页面。
