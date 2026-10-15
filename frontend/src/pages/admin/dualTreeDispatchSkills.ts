// 调度系统领域下面，先有一个统筹技能，领导挂在这里。
// 下面才是 AGV 行业里调度系统要的具体技能。界面以后再挂到具体技能下。
// 进度记在具体技能上，统筹少长一点，只看成长，不参加派单。

export interface SkillSeed {
  name: string;
  description: string;
  children?: SkillSeed[];
}

export interface SkillNodeLike {
  id: string;
  parentId: string | null;
  kind: string;
  name: string;
  description: string;
}

export interface LeafOwner {
  name: string;
  userId: string;
}

const REPLACABLE_DESCRIPTIONS = new Set([
  '单还没真正进调度，或流程、上游回调卡住。',
  '单已进调度，还没分到车上。界面常是长时间「调度中」。',
  '已经分到车，路线没生成或下发不对。界面常是「规划路径中」。蓝色是算完还没下发，绿色是已经下发。',
  '该充电不充、该休息不休息，或休息充电策略没配上。',
  '路线有了，车收不到，或某家车的适配对不上。手持平板下发也在这一站。',
  '车不在路网上、地图不连通，或地图编辑本身打不开。和路径没算出来分开。',
  '库位、库区、载具或车体尺寸不对。两车看着有距离却挨在一起，先看车体尺寸。',
  '门、电梯、输送线或协议不动。先看通信内容，再看事件和任务。',
  '页面白屏、看板不刷新，或报表数据不对。不是任务和路径没算出来。',
  '仿真环境里的任务、车和外设。',
  '用回放和日志把问题定位出来。这一站不负责把任务或路径算出来。',
  '语言、角色和用户权限。',
  '产品设计、需求、评审和发版。报障不因为可以分流就派到这里。',
  '远程服务器部署。',
  '把单送进调度，并把上游回调和流程跑通。',
  '在仿真里把任务和车跑起来。',
  '在仿真里把任务和车跑起来，用来练上面这几项。',
  '领导看分车、规划、充电休息是不是同一类问题。专人挂在下面某一块。',
  '让不同的车和手持端真正收到调度的结果。',
  '库位、载具和车体尺寸。尺寸不对时，看起来会像车没有躲开。',
  '把下面这些技能放在一起看，判断一张单卡在哪一项。领导挂在这里。',
  '顺着一张单看：有没有进任务池，车有没有上轨，然后才是分车、算路、下发。领导挂在这里。',
  '先分清机械、驱动、电气还是安全件。撞门、充不进电也可能连带现场环境。领导挂在这里。',
  '定位、感知、运动控制与车载协议栈。定位丢了先分清算法、传感器还是现场反光。领导挂在这里。',
  '现场无线、有线与链路。整片区掉线和单车掉线先分开。领导挂在这里。',
  'WMS/MES、PLC/产线信号、许可与部署。单没进调度池时，先别怪分车。领导挂在这里。',
  '地面、光照、障碍、温湿度粉尘。现象像车坏或定位丢时，先排除现场。领导挂在这里。',
  '现场部署、调试工具与交付流程。领导挂在这里。',
  '方案、选型、报价与交付前咨询。领导挂在这里。',
  '没有路网就没有调度。先会看地图，再改地图，再判断车能不能走。',
  '浅：节点、边、单向、工位。地图在调度里先变成一张图。',
  '中：把现场画进调度，保存、导入，处理地图打不开。',
  '深一些：限速、禁行、车体过不过得去。',
  '上游的订单怎么变成调度里的一张单，做完怎么告诉上游。',
  '浅：搬运、充电、移库，一张单从哪到哪。',
  '中：待分配、执行中、完成、失败，卡在哪一步。',
  '深一些：哪张单先做，紧急单能不能插进来。',
  '深一些：状态和回调回到 WMS、MES 或产线。',
  '行业里调度最核心的一块：选车、算路、管交通、管电。由浅入深挂在下面。',
  '哪张单给哪台车。',
  '浅：谁近、谁的单更急，先派谁。',
  '中：电不够不派，车型或载具不匹配不派。',
  '深：一批单一起看，避免有的车累死、有的车闲着。',
  '车已经分到了，路怎么算。',
  '浅：A*、Dijkstra，一台车从起点到终点。',
  '中：路堵了、车故障了，换一条路。',
  '深：时间窗、多车同时算，路不要叠在一起。',
  '深：电梯、提升机、跨楼层。',
  '多台车不要撞、不要卡死。',
  '浅：窄道、路口同一时间只进一台。',
  '中：虚拟红绿灯，谁先过。',
  '深：几台车互相等待成环，要能看出来并解开。',
  '车什么时候去充电、休息，不能为了充电把路堵死。',
  '浅：电量低了回去充电。',
  '中：充电可以插到搬运中间，已经取到货的不要乱停。',
  '深：充电位、休息点一起排，避免都挤去充电。',
  '调度和车、上游、设备说话的报文。协议不管算法，但没有它车收不到路。',
  '浅：能把订单发出去，能收回车的状态。',
  '中：取货、放货、充电，以及限速区、禁行区。',
  '深：车必须走的那一段，和还可以改的那一段。VDA5050 的 base 与 horizon 在这里。',
  '不同厂家的车怎么接进同一套调度。',
  '连接断了，指令下不去，状态也上不来，看起来像车不动。',
  '尺寸、货叉、能做的动作不一样。',
  '浅：一种车能收路、能走、能回报。',
  '中：尺寸、货叉、能做的动作不一样。',
  '深：自研、科钛、睿芯行、有光等同时在一张地图上。',
  '货放在哪、车搬的是什么、车体有多大。',
  '浅：库位长宽高、操作高度，载具有多大。',
  '中：这种车能不能搬这种货。',
  '深：一个库位多种车、多种载具、多个停车点。',
  '门、电梯、输送线怎么被调度叫起来。',
  '浅：到了门口让门开，到了电梯让电梯来。',
  '中：按协议把通信内容配出来。',
  '深：什么任务、走到哪一步才触发设备。',
  '先在仿真里把浅的技能跑通，再把多车和异常放进去。',
  '浅：一辆车按路线走完一单。',
  '中：多车同时走，看会不会抢路。',
  '深：用完成时间、绕路、等待来判断算法好不好。',
  '现场出了事，先看状态，再看回放和日志，最后说清卡在哪项技能。',
  '浅：车在哪、单停在哪、系统是否还活着。',
  '中：回到那个时间点，把调度和车的日志对上。',
  '深：这不是笼统的调度故障，而是算法、地图、协议或设备里的一项。',
  '浅：点、节点、边。同一种车一张有向图，不同车型不要混用。',
  '中：手动画、保存、导入。地图打不开先看这里。',
  '中：可走区域和库区生成出来。库位过小、道路缺失、生成转圈，都在这一层。',
  '中：车的物理位置落到节点上。落不上，分配和规划都不会开始。',
  '深：图不连通时，孤立点和断线会被裁掉。充电点或任务点可能因此到不了。',
  '深：限速、禁行、避障、车体过不过得去。距离还没算好时，查出来的远近不可信。',
  '中：任务流把单推进调度。池里没有这张单，就不要先怪分车。',
  '浅：上轨、电量、机器人组、楼层。不够格的车不会进候选。电量低是该充电，不是该派业务单。',
  '中：同一优先级里，一批车和一批单一起算最优，也可以选择不派。车上轨点不在图上，这台车就不可行。',
  '深：去另一层的收益要大于转移代价才过去，并占住名额，避免同一台车被派两次。',
  '浅：按行驶时间找路，不是只看距离。目标点不在图上就没有路。',
  '中：没上轨、没有当前子任务、车型是空的，这台车会被跳过，看起来像没给它算路。',
  '深：节点、边、点位被占住时不能穿过去。路看着不合理，先看是不是被挡住。',
  '深：路算出来还要收下去发给车。蓝色是算完没下发，绿色是已经下发。没收下就还停在规划。',
  '深：电梯、提升机。人过去了，目标层还要能规划出路。',
  '浅：忙时低电、闲时补电、紧急充电是三档。自动充电关了，所有充电都不会触发。',
  '中：充电和休息可以让出来接业务单。已经取到货的不要乱停。',
  '中：没单的车回到休息点。没有休息点，车就停在路中间。',
  '深：有没有空桩、会不会把别的车从桩上请走。充电不要把路口堵住。',
  '浅：现场用平板把单送进来。和车收不收得到路是两件事。',
  '中：调度发标准订单，适配器转成厂家自己的协议再给车；车的状态再转回标准格式。',
  '深：连接断了，指令下不去，状态也上不来，看起来像车不动。',
  '浅：门、电梯、输送线、交通灯。门控关了，车会在门口一直等。',
  '先在仿真里把浅的技能跑通，再把多车和异常放进去。',
  '中：回到那个时间点，把当时的路径和状态看一遍。',
  '深：车不动、单分不出去、该充电不充、跨层不走、地图不连通。先判断是哪一段。',
  '深：用车号、任务号、子任务号、地图号，把任务流、调度、算法和适配器对上。',
  '深一些：做完经回调回到 WMS 或产线。回调积压时，调度里已经结束，上游还不知道。',
  '单已经在池里、车也上了轨之后：选车、算路、管交通、管电。',
  '哪张单给哪台车。先看这台车够不够格，再一批一起算，跨层另算。',
  '车已经分到了，路怎么算、算完有没有真正发出去。',
  '多台车不要撞、不要卡死。',
  '车什么时候去充电、休息，不能为了充电把路堵死。',
  '没有路网就没有调度。先看懂图，再改图，再确认车能落在节点上、图是通的。',
  '图不连通时，孤立点和断线会被裁掉。充电点或任务点可能因此到不了。',
  '可走区域和库区生成出来。库位过小、道路缺失、生成转圈，都看这里。',
  // 六域旧版短描述：细节已并入详情，允许被新种子覆盖
  '先分清是机械、电气还是安全件。现象像撞门、充不进电时，也可能连带现场环境。领导挂在这里。',
  '车架、货叉、顶升、导向、外壳。异响、卡死、变形先看这里。',
  '电机、减速机、差速/舵轮。走不动、打滑、单侧无力。',
  '电池、BMS、充电接触、线束与 IO。充不进电、突然掉电先看这里。',
  '急停、防撞条、安全激光、指示灯。触发后车不能动，先确认是不是真触发。',
  '定位、感知、运动控制与车载协议栈。定位丢了先分清是算法、传感器还是现场反光。领导挂在这里。',
  'SLAM、重定位、里程计、地图匹配。车不知道自己在哪。',
  '雷达、相机、超声与障碍判定。误停、漏检先看这里。',
  '控制器、轨迹跟踪、货叉/顶升控制指令。',
  '车上协议与网关。和「现场 WiFi/交换机」分开：这里是车端怎么说话。',
  '现场无线、有线与链路质量。整片区掉线和单车掉线先分开。领导挂在这里。',
  'WiFi 覆盖、漫游、AP。车走走停停、进某区就断，先看无线。',
  '交换机、VLAN、端口。基站侧、机房侧断连。',
  '丢包、延迟、MQTT/业务通道断。看起来像车不动或调度收不到状态。',
  'WMS/MES、PLC/产线信号、许可与部署。单没进调度池时，先别怪分车。领导挂在这里。',
  '上位下单、回传、状态机是否对齐。',
  '进车/出车、呼叫、联锁。和调度外设「叫门叫梯」相邻，但责任在对接侧信号。',
  'license、证书、对接账号权限。',
  '对接环境、配置项、版本。换环境后接口全挂先看这里。',
  '地面、光照、障碍、温湿度粉尘。现象像车坏或定位丢时，先排除现场。领导挂在这里。',
  '坡度、坑洼、油污、狭窄。撞门、打滑常从这里起。',
  '强光、玻璃、金属反光影响定位与视觉。',
  '临时堆放、人流、叉车混行。',
  '影响传感器、电池与电气的环境条件。',
]);

export const DISPATCH_SKILL_SEEDS: SkillSeed[] = [
  {
    name: '调度统筹',
    description:
      '管调度系统内部一张单怎么走完：进没进池、车上没上轨、分没分到车、路有没有算出来并真正下发。不管车体零件、现场 WiFi、上位 WMS 字段本身。\n'
      + '分流顺序：没图/落不上点 → 地图处理；单还没进池或回调不对 → 任务编排；已进池再谈分配/规划/锁区/充电 → 调度算法；车收不到标准结果 → 车端对接；库位载具尺寸 → 库位与载具；门梯输送线 → 外设控制；先仿真验证 → 仿真验证；先看状态回放再定性 → 运行诊断。\n'
      + '易误派：池里没有单不要怪分车；蓝色路径停在规划多半是下发；整片掉线先网络；上位回传对不齐先上游对接。\n'
      + '专人挂下面具体技能；领导挂这里看整条调度链路，统筹不参加具体派单。',
    children: [
      {
        name: '地图处理',
        description: '先有一张能用的图：看懂、画出来、生成、评估，再确认车能落在节点上。',
        children: [
          { name: '路网拓扑', description: '点、节点、边。同一种车一张有向图，不同车型不要混用。' },
          { name: '绘制地图', description: '手动画、保存、导入。地图打不开先看这里。' },
          { name: '地图生成', description: '从点云、图纸或扫描生成可供调度使用的路网。' },
          { name: '自动地图', description: '卡车、集装箱扫进来之后，在底图上补临时库位和进出的路。' },
          { name: '地图评估', description: '看一张图通不通、代价高不高、好不好调度。这一步不改图。' },
          { name: '地图优化', description: '在已有图上改连通、边的代价、单向和禁行。' },
          { name: '上轨定位', description: '车的物理位置落到节点上。落不上，分配和规划都不会开始。' },
          { name: '连通性校验', description: '按车型检查图是不是强连通。不连通时指出孤立点、断线和车型没启用，不直接改图。' },
          { name: '路网约束', description: '限速、禁行、避障、车体过不过得去。' },
        ],
      },
      {
        name: '任务编排',
        description: '上游的订单怎么变成调度里的一张单。单没进池，就还不是分配的问题。',
        children: [
          { name: '订单拆任务', description: '搬运、充电、移库，一张单从哪到哪。' },
          { name: '任务入池', description: '任务流把单推进调度。池里没有这张单，就不要先怪分车。' },
          { name: '任务状态', description: '待分配、执行中、完成、失败，卡在哪一步。' },
          { name: '优先级与插单', description: '哪张单先做，紧急单能不能插进来。' },
          { name: '上游回传', description: '做完经回调回到 WMS 或产线。回调积压时，调度里已经结束，上游还不知道。' },
        ],
      },
      {
        name: '调度算法',
        description: '单进了池、车上了轨之后，按算法板上的模块来：分配、规划、锁区、充电、预处理。',
        children: [
          {
            name: '任务分配',
            description: '哪张单给哪台车，以及车要不要跨层过去。',
            children: [
              { name: '业务分配', description: '一批车和一批单一起匹配。占用要看当前任务和车上的任务栈。电量不够是去充电，不是派业务单。' },
              { name: '任务分配优先级', description: '多单抢同一台车时谁先配上：等级、紧迫、插单。' },
              { name: '任务间依赖链', description: '前一张做完才能做下一张，或多段取送必须同一台车。' },
              { name: '资源平衡', description: '同层派单通常不跨层。把车和充电机会调到另一层，要收益大于代价。' },
            ],
          },
          {
            name: '路径规划',
            description: '单车先有路。多车分成各自算，和一起算。',
            children: [
              { name: '单车路径规划', description: '一台车按行驶时间找路，每一步带上占地区域。没上轨、没有当前子任务、车型是空的，这台车不会进入规划。' },
              { name: '分布式多车路径规划', description: '每台车各自算最短路。规划时不提前占住别人的点。' },
              { name: '中心化多车路径规划', description: '多台车一起算，时间上不要撞。冲突分点、边和跟随，再看占地区域交不交。' },
              { name: '路径下发', description: '路算出来还要收下去发给车。蓝色是算完没下发，绿色是已经下发。没收下就还停在规划。' },
              { name: '跨楼层行为', description: '一层一张图，层与层没有边，靠电梯口衔接。过去之后，目标层还要能规划。' },
              { name: '目标点不可达', description: '走不到时要指出断在哪、车体占住哪里。不能只给一个失败。' },
            ],
          },
          {
            name: '交通管制',
            description: '锁区、让行，以及把路授出去之前不要绕成环。',
            children: [
              { name: '动态锁区', description: '禁行、消防、第三方混行、障碍物。混行区规划可以穿，执行前要向对方申请。' },
              { name: '路径通行优先级', description: '窄道、跟随、交会时谁先走。这是路上的让行，不是谁先接单。' },
              { name: '无环检查', description: '各自算完最短路之后，真正授出去之前要确认几台车不会互相等成环。' },
            ],
          },
          {
            name: '能量管理',
            description: '什么时候去充电、什么时候回休息点。桩被占住时不能当它是空的。',
            children: [
              { name: '忙时充电', description: '单多的时候，只在电量低到快不能接单时才去充电。' },
              { name: '闲时充电', description: '单少而且空闲够久，就把电补高，免得下一波高峰掉下去。' },
              { name: '时段充电', description: '班前班后按时间窗把电量整体抬高。选桩仍走充电策略。' },
              { name: '休息策略', description: '没单的车回到休息点。电量偏低时先走充电，不走休息。' },
            ],
          },
          {
            name: '地图预处理',
            description: '规划的时候不重算几何，只查事先落好的表。',
            children: [
              { name: '扫掠体与距离', description: '每条边车体会占住的区域，以及按行驶时间算好的距离。表没就绪时，查出来的远近不可信。' },
              { name: '冲突表', description: '两条边的占地区域是否相交。多车会不会碰，规划时查这张表。' },
              { name: 'L-MAPF', description: '先算待命外形，再算沿边扫过的外形，并把两端待命外形并进这条边。' },
              { name: '载具分级', description: '车型和载具组合太多时，按外形并成少数几级，再算占区。' },
            ],
          },
          {
            name: '环境建模',
            description: '规划看到的不是一个点，是车、货和现场里不动的约束。',
            children: [
              { name: '机器人实体', description: '车在路网上走。障碍状态下可以外扩占用，点边结构不变。' },
              { name: '载具合并外形', description: '带上货之后按车型和载具的合并外形判路。不挡车、但挡货，也过不去。' },
              { name: '静态实体', description: '墙、柱、长期禁行。给路径和分配一个稳定的背景。' },
            ],
          },
        ],
      },
      {
        name: '标准协议',
        description: '调度和车、上游、设备说话的报文。协议不管算法，但没有它车收不到路。',
        children: [
          { name: '订单与状态', description: '能把订单发出去，能收回车的状态。' },
          { name: '动作与区域', description: '取货、放货、充电，以及限速区、禁行区。' },
          { name: '已确认路线', description: '车必须走的那一段，和还可以改的那一段。VDA5050 的 base 与 horizon 在这里。' },
        ],
      },
      {
        name: '车端对接',
        description: '适配层。调度的标准订单和各厂家私有协议互转。每一种车一个适配器，负责人可以不同。领导挂在这里。',
        children: [
          { name: '自研车适配', description: '海豚之星（EP）自己的适配器。标准订单转成自研协议下发，车的状态再转回。' },
          { name: '科钛适配', description: '科钛（Cotek）自己的适配器。报文和连接与别的车分开。' },
          { name: '睿芯行适配', description: '睿芯行（RBrain）自己的适配器。报文和连接与别的车分开。' },
          { name: '有光适配', description: '有光自己的适配器。报文和连接与别的车分开。' },
          { name: '连接保持', description: '各适配器连自己的 MQTT。断了就指令下不去、状态上不来，看起来像车不动。' },
          { name: '手持下发', description: '现场用平板把单送进来。和车收不收得到路是两件事。' },
        ],
      },
      {
        name: '库位与载具',
        description: '货放在哪、车搬的是什么、车体有多大。',
        children: [
          { name: '库位与尺寸', description: '库位长宽高、操作高度，载具有多大。' },
          { name: '车与载具匹配', description: '这种车能不能搬这种货。' },
          { name: '车型差异', description: '尺寸、货叉、能做的动作。这是车型库，不是某一种车的协议适配。' },
          { name: '多点接驳', description: '一个库位多种车、多种载具、多个停车点。' },
          { name: '定位点推算', description: '同一库位，不同车型、不同载具，停车点不一样。沿出入库方向用少量参数算出来。' },
        ],
      },
      {
        name: '外设控制',
        description: '门、电梯、输送线怎么被调度叫起来。',
        children: [
          { name: '设备开关', description: '门、电梯、输送线、交通灯。门控关了，车会在门口一直等。' },
          { name: '设备报文', description: '按协议把通信内容配出来。' },
          { name: '设备时机', description: '什么任务、走到哪一步才触发设备。' },
        ],
      },
      {
        name: '仿真验证',
        description: '先在仿真里把一辆车跑通，再放进多车和异常。',
        children: [
          { name: '单车仿真', description: '一辆车按路线走完一单。' },
          { name: '多车仿真', description: '多车同时走，看会不会抢路。' },
          { name: '调度评价', description: '用完成时间、绕路、等待来判断算法好不好。' },
        ],
      },
      {
        name: '运行诊断',
        description: '先看状态，再按症状判断该看任务流、分配、规划、地图还是适配器，最后用车号和任务号把几段对上。',
        children: [
          { name: '状态看板', description: '车在哪、单停在哪、系统是否还活着。' },
          { name: '回放与日志', description: '回到那个时间点，把当时的路径和状态看一遍。' },
          { name: '按症状分流', description: '车不动、单分不出去、该充电不充、跨层不走、地图不连通。先判断是哪一段。' },
          { name: '跨服务串联', description: '用车号、任务号、子任务号、地图号，把任务流、调度、算法和适配器对上。' },
        ],
      },
    ],
  },
];

export const FUNCTION_TO_SKILL: Record<string, string> = {
  流程管理: '上游回传',
  流程模板: '订单拆任务',
  任务回调: '任务状态',
  回调管理: '上游回传',
  任务长期调度中: '业务分配',
  任务下发: '任务入池',
  任务模板: '订单拆任务',
  任务执行顺序: '任务分配优先级',
  机器人组: '任务分配优先级',
  任务长期路径规划中: '单车路径规划',
  '路径规划-蓝色路径': '路径下发',
  '路径规划-绿色路径': '路径下发',
  '路径规划-路径闪烁': '路径下发',
  '路径规划-合理性': '中心化多车路径规划',
  单车路径规划: '单车路径规划',
  '路径规划-提升机、电梯': '跨楼层行为',
  '路径规划-跨楼层': '跨楼层行为',
  上轨锁区: '动态锁区',
  任务自动充电: '忙时充电',
  任务自动休息: '休息策略',
  配置管理: '时段充电',
  网络通信: '连接保持',
  适配器管理: '车端对接',
  车端对接自研车: '自研车适配',
  车端对接科钛: '科钛适配',
  车端对接睿芯行: '睿芯行适配',
  车端对接有光: '有光适配',
  PDA手持操作平板: '手持下发',
  地图编辑: '绘制地图',
  '地图编辑-AI地图': '地图生成',
  库区分支: '路网拓扑',
  库区避障: '路网约束',
  库位管理: '库位与尺寸',
  '库位管理-可视化编辑': '库位与尺寸',
  库区管理: '库位与尺寸',
  库位类型管理: '库位与尺寸',
  库位参数参考模板: '定位点推算',
  载具类型管理: '库位与尺寸',
  载具管理: '库位与尺寸',
  '接驳/多载具配置': '多点接驳',
  机器人类型载具关系: '车与载具匹配',
  机器人类型: '路网约束',
  车型库: '车型差异',
  外设管理: '设备开关',
  外设事件: '设备时机',
  外设任务: '设备开关',
  任务模拟器: '多车仿真',
  仿真外设管理: '多车仿真',
  仿真机器人管理: '单车仿真',
  平台状态: '状态看板',
  数据概览: '状态看板',
  指标库: '调度评价',
  地图库: '路网拓扑',
  场景库: '多车仿真',
  机器人报表: '状态看板',
  回放管理: '回放与日志',
  实时日志管理: '跨服务串联',
  归档日志管理: '跨服务串联',
  性能检测: '按症状分流',
  异常检测: '按症状分流',
  审计日志: '状态看板',
  调度内部异常管理: '按症状分流',
};

export const INTERFACE_DETAIL_NAMES = [
  ...Object.keys(FUNCTION_TO_SKILL),
  '语言管理', '角色管理', '用户管理', '产品设计', '需求管理', '需求评审', '版本发布管理', '远程服务器部署',
  '首页', '监控', '任务', '编排', '设备', '库位', '数据', '运维', '设置界面', 'PDA', '产品', '服务器部署',
  '页面与报表', '运维定位', '账号', '需求侧',
  '任务执行', '现场对象', '看清现场', '交付系统',
  '现场数据', '外部接入', '系统交付',
  '任务流', '充电与休息', '地图', '外设', '仿真',
  '运行呈现', '问题定位', '系统配置', '产品定义', '部署',
  '最近车与优先级', '电量与车型', '多车代价', '动态改路', '归到技能',
  '分配资格', '匈牙利匹配', '跨层拍卖', '单车最短路', '规划资格', '多车无冲突', '跨层路径',
  '区域互锁', '路口放行', '死锁解除', '低电回充', '充电打断任务', '空闲休息', '充电位与交通',
  '强连通', 'AI生成地图',
  '单车跑通', '报文互转', '多厂家并存',
].filter((name) => !seedNames(DISPATCH_SKILL_SEEDS).has(name));

export function skillForFunction(functionName: string) {
  return FUNCTION_TO_SKILL[functionName] || '';
}

function seedNames(seeds: SkillSeed[], found = new Set<string>()): Set<string> {
  seeds.forEach((seed) => {
    found.add(seed.name);
    if (seed.children) seedNames(seed.children, found);
  });
  return found;
}

function belongsTo(nodes: SkillNodeLike[], nodeId: string, ancestorId: string) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  let cur = byId.get(nodeId);
  const guard = new Set<string>();
  while (cur && !guard.has(cur.id)) {
    if (cur.id === ancestorId) return true;
    guard.add(cur.id);
    cur = cur.parentId ? byId.get(cur.parentId) : undefined;
  }
  return false;
}

export function ensureSkillSeeds<T extends SkillNodeLike>(
  nodes: T[],
  domainName: string,
  seeds: SkillSeed[],
  create: (parentId: string, name: string, description: string) => T,
): T[] {
  const domain = nodes.find((node) => node.kind === 'domain' && node.name === domainName);
  if (!domain) return nodes;
  const next = nodes.map((node) => ({ ...node }));
  const ensure = (parentId: string, seed: SkillSeed) => {
    const found = next.find((item) => (
      item.kind === 'skill' && item.name === seed.name && belongsTo(next, item.id, domain.id)
    ));
    let node = found;
    if (!node) {
      node = create(parentId, seed.name, seed.description);
      next.push(node);
    } else {
      if (node.parentId !== parentId) node.parentId = parentId;
      const blank = !node.description.trim();
      if (blank || REPLACABLE_DESCRIPTIONS.has(node.description)) node.description = seed.description;
    }
    (seed.children || []).forEach((child) => ensure(node.id, child));
  };
  seeds.forEach((seed) => ensure(domain.id, seed));
  return next;
}

export function dropInterfaceDetails<T extends SkillNodeLike>(
  nodes: T[],
  domainName: string,
  names: string[],
): T[] {
  const domain = nodes.find((node) => node.kind === 'domain' && node.name === domainName);
  if (!domain) return nodes;
  const detail = new Set(names);
  let current = nodes.map((node) => ({ ...node }));
  let changed = true;
  while (changed) {
    changed = false;
    const drop = new Set(
      current
        .filter((node) => (
          node.kind === 'skill'
          && detail.has(node.name)
          && belongsTo(current, node.id, domain.id)
          && !current.some((child) => child.parentId === node.id)
        ))
        .map((node) => node.id),
    );
    if (drop.size === 0) break;
    current = current.filter((node) => !drop.has(node.id));
    changed = true;
  }
  return current;
}

export function dropRolledSpecialists<TMount extends { nodeId: string; userId: string; role: string }>(
  nodes: SkillNodeLike[],
  mounts: TMount[],
  userIds: string[],
  domainName: string,
): TMount[] {
  const domain = nodes.find((node) => node.kind === 'domain' && node.name === domainName);
  if (!domain) return mounts;
  const owners = new Set(userIds);
  return mounts.filter((mount) => {
    if (mount.role !== 'specialist' || !owners.has(mount.userId)) return true;
    const node = nodes.find((item) => item.id === mount.nodeId);
    if (!node || node.kind !== 'skill') return true;
    return !belongsTo(nodes, node.id, domain.id);
  });
}

export function applyLeafSpecialists<TMount extends { nodeId: string; userId: string; role: string }>(
  nodes: SkillNodeLike[],
  mounts: TMount[],
  owners: LeafOwner[],
  domainName: string,
  create: (nodeId: string, userId: string) => TMount,
): TMount[] {
  const domain = nodes.find((node) => node.kind === 'domain' && node.name === domainName);
  if (!domain) return mounts;
  const next = [...mounts];
  owners.forEach((owner) => {
    const leaf = nodes.find((node) => (
      node.kind === 'skill' && node.name === owner.name && belongsTo(nodes, node.id, domain.id)
    ));
    if (!leaf || !owner.userId) return;
    const exists = next.some((mount) => mount.nodeId === leaf.id && mount.userId === owner.userId);
    if (!exists) next.push(create(leaf.id, owner.userId));
  });
  return next;
}
