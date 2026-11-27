本次面聊主要围绕将禅道功能迁移至服务号，并针对研发团队的实际需求进行功能规划。讨论了工单系统、版本管理、需求/任务/bug单流转、文档与接口文档、版本说明、草稿状态及指派等功能。初步结论是先开发一版低配版以打通流程，后续根据使用反馈持续迭代优化。

### **1. 禅道功能迁移至服务号的总体目标**

- **核心目标**：将禅道的需求、任务、bug管理等核心功能迁移至服务号，并在此基础上增加研发专属的USP界面，提升研发效率。[1](wxwork://jump?target=voice_chat_msg&scheme=EZwtMO2GkNIaoKxXjSU43xB4IFNJJZ4MnpkFuLmkO1w)[2](wxwork://jump?target=voice_chat_msg&scheme=X4q6rpHBbOzynKGqpRkwPKHxXIMuSY_5EdRciHqmXtg)[3](wxwork://jump?target=voice_chat_msg&scheme=3YST9KmAYA3GAlYu1Zq1BwTEdHBDWClOJ50fY5JtLhs)
- **实施策略**：先开发一个“低配版”的简易系统，确保基本流程能够跑通，后续在实际使用中根据反馈不断调整和完善。[4](wxwork://jump?target=voice_chat_msg&scheme=E-XWQG_u7qBduN67bynxeylNkdxjPkL61dvC5yMGG1s)[5](wxwork://jump?target=voice_chat_msg&scheme=KkwV601UmYo6WDbN1d58N_nQrI__H9lF5kybDW3CQAA)[6](wxwork://jump?target=voice_chat_msg&scheme=fSqOSjz3OTc6W83z8IVPEUSueRjGP9Uhe022szmJKYg)

### **2. 研发工单与版本管理功能**

- **工单结构**：采用父子工单模式，一个父工单下可包含多个子工单，所有子工单完成后父工单才算结束，适用于多人协同场景。[7](wxwork://jump?target=voice_chat_msg&scheme=Wd22tfLqsifaQkhRnUQH6phXiAFJ7FCEAjIS-cmOP4g)
- **版本关联**：每个版本需关联对应的项目、bug以及版本简要说明，说明内容包括升级后现场需要做的配置调整（如地图预处理、充电桩配置等）。[3](wxwork://jump?target=voice_chat_msg&scheme=3YST9KmAYA3GAlYu1Zq1BwTEdHBDWClOJ50fY5JtLhs)[8](wxwork://jump?target=voice_chat_msg&scheme=Ws_qVcKAXs3WscGl28LPnOT4ETDMgozPWf-oxVq-Rak)[9](wxwork://jump?target=voice_chat_msg&scheme=L8zDug8JkdWxBDgHu-1DLwKkL82GBUQCeTT4fX4G8m4)[10](wxwork://jump?target=voice_chat_msg&scheme=DsZyChWsHA2wnEppR1LH9FH0JAFYCSTQ0zwvMpLZ0SU)[11](wxwork://jump?target=voice_chat_msg&scheme=VSWDGII0iqVThWgdoMTX7NBf4BZsspVLT7d3X58JsZc)[12](wxwork://jump?target=voice_chat_msg&scheme=sqOIPLkq6Fi65KnMdlpOdkGmN4URITi4JafYr8S7XLQ)
- **版本历史查看**：需要提供历史版本的查询功能，以便追溯每个版本包含的功能及其完成状态。[13](wxwork://jump?target=voice_chat_msg&scheme=6b2LtIiaXX5pFb13cQYEDaLMii2N66dnQJlryNLyhp0)[14](wxwork://jump?target=voice_chat_msg&scheme=vNjQXQ4tBckAfLBkzTTH6Y2nQtqZgdlNFVczwSDl1RE)[15](wxwork://jump?target=voice_chat_msg&scheme=5UQ5woWmoqaCuPiUwp4MwclV5CqAJR-O6I-JqMYcMNU)[16](wxwork://jump?target=voice_chat_msg&scheme=7sTqiTl7U0DmVi4A1zWp6Rvm4F9-V_F54o6HxrBEj8o)

### **3. 需求/任务/bug单的流转与状态管理**

- **单据分类**：明确区分需求单、任务单和bug单，三者独立管理但又相互关联。[17](wxwork://jump?target=voice_chat_msg&scheme=FXqWBTl728RJvysYnPoapjnmklrtL_D88cYwZmjpOFA)[18](wxwork://jump?target=voice_chat_msg&scheme=NpEmdAZliBAmOUJGApfAJ1MVTCkXXDEf0Vclz1wp3tk)[19](wxwork://jump?target=voice_chat_msg&scheme=gz1MAZXO1BwdxUPhU-fWspqFg2zNvEs0sIPW59cJT_I)
- **状态变更问题**：当前禅道存在状态变更后无法回退的问题（如未评审却已进入研发阶段），新系统需避免此类刚性绑定，允许灵活调整状态。[20](wxwork://jump?target=voice_chat_msg&scheme=RgUTDwAYLh1VcrBJmZ4u3j5YWsNJFe73Y6EK075XJU0)[21](wxwork://jump?target=voice_chat_msg&scheme=B0dpRjX6gemB102r0meD8ueTSXE0oFRRZFipIb2BBs4)[22](wxwork://jump?target=voice_chat_msg&scheme=t-XJeNes4xCOvh-7XblXORvsQzJacsMafzjLeHxEc6o)[23](wxwork://jump?target=voice_chat_msg&scheme=WYK9C2GXDK6tny1Zn8_tfbVaCW2dr1LMlMZtCK6I9_k)[24](wxwork://jump?target=voice_chat_msg&scheme=yPimpSI2YikqABqMGcqqdpVLiozceB0CJuh5Uk99IiE)
- **通知机制**：产品经理创建需求后，可通过在工单内@相关人员的方式通知，并在工单下方直接进行讨论，讨论确定后再由开发创建子任务执行。[25](wxwork://jump?target=voice_chat_msg&scheme=8LI88_YnjKnhCVqJ7ZSbliIy_Cg3JNWIzOgM2Xm6hzY)[26](wxwork://jump?target=voice_chat_msg&scheme=IVBBfx7Isq4eh6KYiQEFR9goiwqVLzMrWFKf2KUNrzk)[27](wxwork://jump?target=voice_chat_msg&scheme=LW2WRTXB7qKu1xwa27xkTYkqeB4icZmKwOarfhu13Ng)[28](wxwork://jump?target=voice_chat_msg&scheme=Dq__kiX1L0tfYurIPXAfzjyNC40ZamWRw-sfH4oKUmw)[29](wxwork://jump?target=voice_chat_msg&scheme=JKqQl7-0ipIXL963ZKeyeqYSTEyvvTpyb7R5e8WPje0)[30](wxwork://jump?target=voice_chat_msg&scheme=KCOJpELQbos3t3ciNxc2R55AbGNJf_7W9xwRsmf9uA0)

### **4. 文档与接口文档需求**

- **技术文档**：研发人员需要一个与版本绑定的技术文档空间，用于记录每个版本新增功能的技术实现方案，区别于产品经理编写的需求文档。[31](wxwork://jump?target=voice_chat_msg&scheme=pKJDEoxZDx5pFGsfUaHqih1-IsR-47X67QB1BeuF4H4)[32](wxwork://jump?target=voice_chat_msg&scheme=kfRO1H4Ax2OuLu39vBTEAAqIb2pJIR9zfGPp6tmJzMA)[33](wxwork://jump?target=voice_chat_msg&scheme=kSAbVOkZeKUa_mZ7fTh8KYA_O1X8wrpYLOrI8gdYhCY)
- **接口文档**：目前接口文档存放在apifox中，但无法直接集成到服务号（因内部接口不公开）。建议将接口信息写入技术文档中，或按模块/岗位（前端、后端、算法）分别展示各自关注的接口。[34](wxwork://jump?target=voice_chat_msg&scheme=Q-AwlJYeD9NWNvo_3u_aC6thTE2wg7mOp_U6VU8D_vk)[35](wxwork://jump?target=voice_chat_msg&scheme=2_mn7qrqLsV_691ubt5u19TtENcdcgarknLNhlMCiug)[36](wxwork://jump?target=voice_chat_msg&scheme=EGy5EUQ9sAWYqkGM0Nlq45hqVdT-aXIwl7hZ0D-_Ny0)[37](wxwork://jump?target=voice_chat_msg&scheme=NKrEBpBXqXk0-zTmeH4Y5Ie9jFuVMWOX3UvEyUp8xqY)[38](wxwork://jump?target=voice_chat_msg&scheme=wmQk1XafQiyAXCNjJvyxID5W6tL_brnko3qoPRJJHx8)[39](wxwork://jump?target=voice_chat_msg&scheme=wl35PECRDgb7cCq1eC1BMFb-E1VRAi2KwWoXDbPoXQ4)

### **5. 草稿状态与指派功能**

- **草稿需求管理**：产品经理在正式发布需求前，常有大量草稿状态的需求需要管理。草稿需求也需要指定负责人（指派人），以便后续筛选和分配任务。[40](wxwork://jump?target=voice_chat_msg&scheme=7JJk47VIcF3WO_k84pUCf90GpjLR9SdtBT-gBsCXv2k)[41](wxwork://jump?target=voice_chat_msg&scheme=YhqbYc2bgZjePgj_2yP582UViWqQRIDgB11h0px8KsQ)[42](wxwork://jump?target=voice_chat_msg&scheme=JyueEeo_s-MLnotZnkyIIQ1tzP2Kd6azCtTjrw2Pux4)[43](wxwork://jump?target=voice_chat_msg&scheme=5AmEQeBL-H5oWRUcgkwSU7QH8AcXqmiZOKwPKkukG3w)[44](wxwork://jump?target=voice_chat_msg&scheme=26YDRtaj-iun96VovTZazXT-pCB_-qKq5wiSafx4gM0)[45](wxwork://jump?target=voice_chat_msg&scheme=h905ysYuFTBAcvZ3CMiquQE9OzvGHYOgLzec7iyq0Ow)
- **指派灵活性**：草稿状态的指派人可以是临时的，后续可能更换，因此指派本身也应视为草稿的一部分，不强制锁定。[46](wxwork://jump?target=voice_chat_msg&scheme=OjPSvRe57psrrpgrLdVanzWNOX-ZBCvEVk9mjil57dg)
- **筛选与视图**：产品经理需要能够按人员筛选出所有未开发的草稿需求，从而判断哪位开发当前空闲并分配任务。[47](wxwork://jump?target=voice_chat_msg&scheme=KDPhZX6uY6f6lDHl6TilXfwjWUp-ru9OD_hRtJhBTss)[48](wxwork://jump?target=voice_chat_msg&scheme=LbpixgUO0pGuam8hbU9AMka3e1Nz9uBAP8JnklrTCmU)[49](wxwork://jump?target=voice_chat_msg&scheme=zpaPOFJQR6OUgWt7lRNonOMx4TmB_LgIOY88r1xQPo8)

### **6. 其他讨论点**

- **题目生成**：为了提升工单质量以生成更好的培训题目，需要收集AGV和USP领域的基础知识模块，由AI生成题目并形成教材。[50](wxwork://jump?target=voice_chat_msg&scheme=oRH7afCvx_PlYExwUZQd474mcNgR3tp9gljt849Tc-E)
- **历史数据迁移**：旧禅道的历史数据暂不一次性迁移，待新系统稳定后再考虑逐步导入。[51](wxwork://jump?target=voice_chat_msg&scheme=60xUlZucwJ1wVViT4ajaa1OJsKJDsAVQTOchuwc9dmk)[52](wxwork://jump?target=voice_chat_msg&scheme=bbR99XAGB34hM5MmYYOeJdVyKv6RGorNidobz5i-FNI)[53](wxwork://jump?target=voice_chat_msg&scheme=oM0JDf5wcOR8ZZs-A1b9v9enUxRTQ6lvDkiCE4N38_0)