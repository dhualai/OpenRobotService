import json
def generate_chart_data(agv_data):
    """生成图表所需的数据格式"""
    # 按机器人分组的数据（用于分组柱状图）
    robot_data = {}
    all_robots = set()
    all_types = set()
    all_groups = set()
    all_names = set()
    
    for item in agv_data['data']:
        robot_id = item['robot_id']
        task_type = item['type']
        count = item['count']
        # 提取或默认为'unknown'的group_id和name
        group_id = item.get('group_id', 'unknown')
        name = item.get('name', 'unknown')
        
        all_robots.add(robot_id)
        all_types.add(task_type)
        all_groups.add(group_id)
        all_names.add(name)
        
        if robot_id not in robot_data:
            robot_data[robot_id] = {}
        
        if task_type not in robot_data[robot_id]:
            robot_data[robot_id][task_type] = 0
        
        robot_data[robot_id][task_type] += count
    
    # 按状态分组的数据（用于饼图）
    status_data = {}
    for item in agv_data['data']:
        status = item['status']
        count = item['count']
        
        if status not in status_data:
            status_data[status] = 0
        status_data[status] += count
    
    # 堆叠柱状图数据（按任务类型分组，堆叠显示各状态）
    type_status_data = {}
    all_statuses = set()
    
    for item in agv_data['data']:
        task_type = item['type']
        status = item['status']
        count = item['count']
        
        all_statuses.add(status)
        
        if task_type not in type_status_data:
            type_status_data[task_type] = {}
        
        type_status_data[task_type][status] = count
    
    # 堆叠柱状图数据（按组分组，堆叠显示各状态）
    group_status_data = {}
    
    for item in agv_data['data']:
        # 提取或默认为'unknown'的group_id
        group_id = item.get('group_id', 'unknown')
        status = item['status']
        count = item['count']
        
        if group_id not in group_status_data:
            group_status_data[group_id] = {}
        
        if status not in group_status_data[group_id]:
            group_status_data[group_id][status] = 0
        
        group_status_data[group_id][status] += count
    

    # 堆叠柱状图数据（按name分组，堆叠显示各状态）
    name_status_data = {}
    direct_O_data = {}
    for item in agv_data['data']:
        # 提取或默认为'unknown'的name
        name = item.get('name', 'unknown')
        status = item['status']
        count = item['count']
        group = item.get('group_id', 'unknown')
        
        if name not in name_status_data:
            name_status_data[name] = {}
        
        if status not in name_status_data[name]:
            name_status_data[name][status] = 0
        
        if name == 'O' and 'A1MJHJ' in group:
            direct_O_data[status] = count
        else:
            name_status_data[name][status] += count
    
    # 计算移库数据（O-ZCO）
    # 先获取O和ZCO类型的任务数据
    move_task_data = {}
    if 'O' in name_status_data and 'ZCO' in name_status_data:
        # 计算O类型的完成+强制完成总数
        o_complete = name_status_data['O'].get('FINISHED', 0) + name_status_data['O'].get('FORCE_FINISH', 0)
        # 计算ZCO类型的完成+强制完成总数
        zco_complete = name_status_data['ZCO'].get('FINISHED', 0) + name_status_data['ZCO'].get('FORCE_FINISH', 0)
        # 计算移库数量（确保非负）
        A1MjHj_complete = direct_O_data.get('FINISHED', 0) + direct_O_data.get('FORCE_FINISH', 0)
        #zco_complete += A1MjHj_complete
        name_status_data['ZCO']['FINISHED'] +=direct_O_data.get('FINISHED', 0)
        name_status_data['ZCO']['FORCE_FINISH'] +=direct_O_data.get('FORCE_FINISH', 0)

        move_count = max(0, o_complete - zco_complete)
        print(f"出库：{zco_complete}，移库：{move_count}，A1MjHj_complete{A1MjHj_complete}")
        if move_count > 0:
            # 将移库数据添加到完成状态
            move_task_data['FINISHED'] = move_count
    # 堆叠柱状图数据（按robot分组，堆叠显示各状态）
    robot_status_data = {}
    
    for item in agv_data['data']:
        robot_id = item['robot_id']
        status = item['status']
        count = item['count']
        
        if robot_id not in robot_status_data:
            robot_status_data[robot_id] = {}
        
        if status not in robot_status_data[robot_id]:
            robot_status_data[robot_id][status] = 0
        
        robot_status_data[robot_id][status] += count
    
    # 处理None值，将None转换为字符串'unknown'后再排序
    def safe_sort(items):
        # 确保所有元素都是字符串类型
        safe_items = [str(item) if item is not None else 'unknown' for item in items]
        return sorted(safe_items)
    

    
    # 如果有移库数据，添加到type_status_data和name_status_data中
    if move_task_data:
        # 同时添加到name_status_data，因为图表使用这个数据
        name_status_data['yk'] = move_task_data
        all_names.add('yk')
    print(name_status_data)
    return {
        'raw_data': agv_data,
        'robot_data': robot_data,
        'all_robots': safe_sort(list(all_robots)),
        'all_types': safe_sort(list(all_types)),
        'all_groups': safe_sort(list(all_groups)),
        'all_names': safe_sort(list(all_names)),
        'status_data': status_data,
        'type_status_data': type_status_data,
        'group_status_data': group_status_data,
        'name_status_data': name_status_data,
        'robot_status_data': robot_status_data,
        'all_statuses': safe_sort(list(all_statuses))
    }

def generate_html_report(chart_data, output_path):
    """生成HTML报告"""
    try:
        print(f"开始生成HTML报告: {output_path}")
        
        # 将数据转换为JSON字符串
        chart_data_json = json.dumps(chart_data, ensure_ascii=False)
        
        # 使用三重引号创建HTML内容
        html_content = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AGV任务统计报告</title>
    <!-- 引入外部资源 -->
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css" rel="stylesheet">
    
    <!-- Tailwind CSS 配置 -->
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        primary: '#4f46e5',
                        secondary: '#7c3aed',
                        success: '#10b981',
                        warning: '#f59e0b',
                        danger: '#ef4444',
                        info: '#3b82f6',
                        dark: {
                            100: '#1e293b',
                            200: '#0f172a',
                            300: '#020617'
                        }
                    },
                    fontFamily: {
                        inter: ['Inter', 'system-ui', 'sans-serif'],
                    },
                },
            }
        }
    </script>
    
    <style type="text/tailwindcss">
        @layer utilities {
            .content-auto {
                content-visibility: auto;
            }
            .card-shadow {
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            }
            .dark-card-shadow {
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -2px rgba(0, 0, 0, 0.2);
            }
            .animate-fadeIn {
                animation: fadeIn 0.5s ease-in-out;
            }
            .animate-slideUp {
                animation: slideUp 0.5s ease-out;
            }
            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }
            @keyframes slideUp {
                from { transform: translateY(20px); opacity: 0; }
                to { transform: translateY(0); opacity: 1; }
            }
        }
    </style>
</head>
<body class="font-inter transition-colors duration-300 bg-gray-50 dark:bg-dark-200 text-gray-800 dark:text-gray-100">
    <!-- 头部导航 -->
    <header class="sticky top-0 z-50 backdrop-blur-md bg-white/90 dark:bg-dark-100/90 border-b border-gray-200 dark:border-gray-800 shadow-sm">
        <div class="container mx-auto px-4 py-3 flex justify-between items-center">
            <div class="flex items-center space-x-3">
                <div class="bg-gradient-to-r from-primary to-secondary w-10 h-10 rounded-lg flex items-center justify-center text-white">
                    <i class="fa fa-cubes text-xl"></i>
                </div>
                <h1 class="text-xl font-bold bg-gradient-to-r from-primary to-secondary text-transparent bg-clip-text">AGV 任务统计平台</h1>
            </div>
            <div class="flex items-center space-x-4">
                <button id="theme-toggle" class="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">
                    <i class="fa fa-moon-o dark:hidden text-xl"></i>
                    <i class="fa fa-sun-o hidden dark:block text-xl"></i>
                </button>
                <button id="refresh-charts" class="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">
                    <i class="fa fa-refresh text-xl"></i>
                </button>
            </div>
        </div>
    </header>

    <main class="container mx-auto px-4 py-8 max-w-7xl">
        <!-- 页面标题与时间范围 -->
        <div class="mb-8 animate-fadeIn">
            <h2 class="text-[clamp(1.5rem,4vw,2.5rem)] font-bold mb-2">AGV 任务统计报告</h2>
            <div class="flex flex-col sm:flex-row sm:items-center text-gray-600 dark:text-gray-400">
                <p class="mb-2 sm:mb-0"><i class="fa fa-calendar mr-2"></i>统计时间范围：<span id="time-range" class="font-medium text-gray-800 dark:text-gray-200"></span></p>
                <p class="ml-0 sm:ml-6"><i class="fa fa-clock-o mr-2"></i>生成时间：<span id="generate-time"></span></p>
            </div>
        </div>

        <!-- 关键指标卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
            <!-- 总计任务数 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">总计任务数</p>
                            <h3 id="total-count" class="text-3xl font-bold text-primary mt-1"></h3>
                        </div>
                        <div class="p-3 bg-primary/10 dark:bg-primary/20 rounded-full">
                            <i class="fa fa-tasks text-primary text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-success text-sm">
                        <i class="fa fa-arrow-up mr-1"></i>
                        <span id="task-growth">查看详细趋势</span>
                    </div>
                </div>
            </div>

            <!-- 活跃机器人 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.1s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">活跃机器人</p>
                            <h3 id="active-robots" class="text-3xl font-bold text-secondary mt-1"></h3>
                        </div>
                        <div class="p-3 bg-secondary/10 dark:bg-secondary/20 rounded-full">
                            <i class="fa fa-android text-secondary text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-check-circle mr-1"></i>
                        <span>实时监控中</span>
                    </div>
                </div>
            </div>

            <!-- 任务类型数量 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.2s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">任务类型数量</p>
                            <h3 id="task-types" class="text-3xl font-bold text-info mt-1"></h3>
                        </div>
                        <div class="p-3 bg-info/10 dark:bg-info/20 rounded-full">
                            <i class="fa fa-sitemap text-info text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-list-ul mr-1"></i>
                        <span>全部类型已加载</span>
                    </div>
                </div>
            </div>

            <!-- 分组数量 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.3s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">分组数量</p>
                            <h3 id="group-count" class="text-3xl font-bold text-success mt-1"></h3>
                        </div>
                        <div class="p-3 bg-success/10 dark:bg-success/20 rounded-full">
                            <i class="fa fa-users text-success text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-sitemap mr-1"></i>
                        <span>机器人分组总数</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- 任务完成统计卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
            <!-- 出库任务 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.4s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">出库任务</p>
                            <h3 id="outbound-finished" class="text-3xl font-bold text-primary mt-1">0</h3>
                        </div>
                        <div class="p-3 bg-primary/10 dark:bg-primary/20 rounded-full">
                            <i class="fa fa-sign-out text-primary text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-check-circle mr-1"></i>
                        <span>包括完成和强制完成</span>
                    </div>
                </div>
            </div>

            <!-- 入库任务 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.5s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">入库任务</p>
                            <h3 id="inbound-finished" class="text-3xl font-bold text-secondary mt-1">0</h3>
                        </div>
                        <div class="p-3 bg-secondary/10 dark:bg-secondary/20 rounded-full">
                            <i class="fa fa-sign-in text-secondary text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-check-circle mr-1"></i>
                        <span>包括完成和强制完成</span>
                    </div>
                </div>
            </div>

            <!-- 移库任务 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.6s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">移库任务</p>
                            <h3 id="move-finished" class="text-3xl font-bold text-warning mt-1">0</h3>
                        </div>
                        <div class="p-3 bg-warning/10 dark:bg-warning/20 rounded-full">
                            <i class="fa fa-exchange text-warning text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-check-circle mr-1"></i>
                        <span>移库任务完成数量</span>
                    </div>
                </div>
            </div>

            <!-- 其他任务 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl overflow-hidden card-shadow dark-card-shadow animate-slideUp transition-all duration-300 hover:scale-[1.02]" style="animation-delay: 0.7s">
                <div class="p-6">
                    <div class="flex justify-between items-start mb-4">
                        <div>
                            <p class="text-gray-500 dark:text-gray-400 text-sm font-medium">其他任务</p>
                            <h3 id="other-finished" class="text-3xl font-bold text-info mt-1">0</h3>
                        </div>
                        <div class="p-3 bg-info/10 dark:bg-info/20 rounded-full">
                            <i class="fa fa-ellipsis-h text-info text-xl"></i>
                        </div>
                    </div>
                    <div class="flex items-center text-gray-500 dark:text-gray-400 text-sm">
                        <i class="fa fa-check-circle mr-1"></i>
                        <span>包括完成和强制完成</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- 图表区域 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
            <!-- 任务状态分布 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp" style="animation-delay: 0.4s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-pie-chart text-primary mr-3"></i>
                        任务状态分布
                    </h3>
                    <div class="flex space-x-2">
                        <button class="status-filter-btn px-3 py-1 text-xs rounded-full bg-primary/10 text-primary hover:bg-primary/20 transition-colors active">全部</button>
                        <button class="status-filter-btn px-3 py-1 text-xs rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">今日</button>
                        <button class="status-filter-btn px-3 py-1 text-xs rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">本周</button>
                    </div>
                </div>
                <div id="status-pie-chart" class="w-full h-[400px]"></div>
            </div>

            <!-- 各机器人任务数量对比 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp" style="animation-delay: 0.5s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-bar-chart text-secondary mr-3"></i>
                        机器人任务数量对比
                    </h3>
                    <div class="relative">
                        <select id="robot-chart-type" class="bg-gray-100 dark:bg-gray-800 border-none rounded-lg px-3 py-1.5 pr-8 text-sm appearance-none focus:outline-none focus:ring-2 focus:ring-primary/50">
                            <option value="bar">柱状图</option>
                            <option value="line">折线图</option>
                            <option value="radar">雷达图</option>
                        </select>
                        <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-gray-500">
                            <i class="fa fa-chevron-down text-xs"></i>
                        </div>
                    </div>
                </div>
                <div id="robot-bar-chart" class="w-full h-[400px]"></div>
            </div>

            <!-- 各任务类型状态分布 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp lg:col-span-2" style="animation-delay: 0.6s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-tachometer text-info mr-3"></i>
                        各任务类型状态分布
                    </h3>
                    <div class="flex items-center space-x-4">
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                            <i class="fa fa-info-circle mr-1"></i> 点击图例可筛选
                        </span>
                        <button id="type-stack-chart-expand" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                            <i class="fa fa-expand"></i>
                        </button>
                    </div>
                </div>
                <div id="type-status-stack-chart" class="w-full h-[450px]"></div>
            </div>
            
            <!-- 机器人组任务状态分布 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp lg:col-span-2" style="animation-delay: 0.7s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-sitemap text-success mr-3"></i>
                        机器人组任务状态分布
                    </h3>
                    <div class="flex items-center space-x-4">
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                            <i class="fa fa-info-circle mr-1"></i> 点击图例可筛选
                        </span>
                        <button id="group-stack-chart-expand" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                            <i class="fa fa-expand"></i>
                        </button>
                    </div>
                </div>
                <div id="group-status-stack-chart" class="w-full h-[450px]"></div>
            </div>
            
            <!-- 各类型任务数量比 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp lg:col-span-2" style="animation-delay: 0.8s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-tags text-warning mr-3"></i>
                        各类型任务数量比
                    </h3>
                    <div class="flex items-center space-x-4">
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                            <i class="fa fa-info-circle mr-1"></i> 点击图例可筛选
                        </span>
                        <button id="name-stack-chart-expand" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                            <i class="fa fa-expand"></i>
                        </button>
                    </div>
                </div>
                <div id="name-status-stack-chart" class="w-full h-[450px]"></div>
            </div>
            
            <!-- 各机器人状态分布 -->
            <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp lg:col-span-2" style="animation-delay: 0.9s">
                <div class="flex justify-between items-center mb-6">
                    <h3 class="text-xl font-bold flex items-center">
                        <i class="fa fa-robot text-secondary mr-3"></i>
                        各机器人状态分布
                    </h3>
                    <div class="flex items-center space-x-4">
                        <span class="text-sm text-gray-500 dark:text-gray-400">
                            <i class="fa fa-info-circle mr-1"></i> 点击图例可筛选
                        </span>
                        <button id="robot-stack-chart-expand" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
                            <i class="fa fa-expand"></i>
                        </button>
                    </div>
                </div>
                <div id="robot-status-stack-chart" class="w-full h-[450px]"></div>
            </div>
        </div>

        <!-- 详细数据表格 -->
        <div class="bg-white dark:bg-dark-100 rounded-xl p-6 card-shadow dark-card-shadow animate-slideUp" style="animation-delay: 0.7s">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-xl font-bold flex items-center">
                    <i class="fa fa-table text-success mr-3"></i>
                    详细统计数据
                </h3>
                <div class="flex items-center space-x-3">
                    <div class="relative">
                        <input id="data-search" type="text" placeholder="搜索数据..." class="bg-gray-100 dark:bg-gray-800 border-none rounded-lg px-3 py-1.5 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50">
                        <i class="fa fa-search absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-500"></i>
                    </div>
                    <button id="export-table" class="flex items-center px-3 py-1.5 bg-success text-white rounded-lg text-sm hover:bg-success/90 transition-colors">
                        <i class="fa fa-download mr-2"></i>
                        导出数据
                    </button>
                </div>
            </div>
            
            <div class="overflow-x-auto">
                <table id="detail-table" class="w-full min-w-[640px]">
                    <thead>
                        <tr class="border-b border-gray-200 dark:border-gray-800">
                            <th class="py-3 px-4 text-left text-sm font-semibold text-gray-600 dark:text-gray-300">
                                <div class="flex items-center cursor-pointer" onclick="sortTable(0)">
                                    机器人ID
                                    <i class="fa fa-sort ml-1"></i>
                                </div>
                            </th>
                            <th class="py-3 px-4 text-left text-sm font-semibold text-gray-600 dark:text-gray-300">
                                <div class="flex items-center cursor-pointer" onclick="sortTable(1)">
                                    任务类型
                                    <i class="fa fa-sort ml-1"></i>
                                </div>
                            </th>
                            <th class="py-3 px-4 text-left text-sm font-semibold text-gray-600 dark:text-gray-300">
                                <div class="flex items-center cursor-pointer" onclick="sortTable(2)">
                                    状态
                                    <i class="fa fa-sort ml-1"></i>
                                </div>
                            </th>
                            <th class="py-3 px-4 text-right text-sm font-semibold text-gray-600 dark:text-gray-300">
                                <div class="flex items-center justify-end cursor-pointer" onclick="sortTable(3)">
                                    数量
                                    <i class="fa fa-sort ml-1"></i>
                                </div>
                            </th>
                            <th class="py-3 px-4 text-center text-sm font-semibold text-gray-600 dark:text-gray-300">操作</th>
                        </tr>
                    </thead>
                    <tbody id="table-body">
                        <!-- 数据行将通过JavaScript动态生成 -->
                    </tbody>
                </table>
            </div>
            
            <!-- 分页控件 -->
            <div class="flex justify-between items-center mt-6">
                <div class="text-sm text-gray-500 dark:text-gray-400">
                    显示 <span id="showing-range">1-10</span> 共 <span id="total-records">0</span> 条
                </div>
                <div class="flex space-x-1">
                    <button class="page-btn px-3 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed" disabled>
                        <i class="fa fa-angle-left"></i>
                    </button>
                    <button class="page-btn px-3 py-1 rounded-md bg-primary text-white">1</button>
                    <button class="page-btn px-3 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">2</button>
                    <button class="page-btn px-3 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">3</button>
                    <span class="px-3 py-1">...</span>
                    <button class="page-btn px-3 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">8</button>
                    <button class="page-btn px-3 py-1 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
                        <i class="fa fa-angle-right"></i>
                    </button>
                </div>
            </div>
        </div>
    </main>

    <footer class="bg-white dark:bg-dark-100 border-t border-gray-200 dark:border-gray-800 py-6 mt-10">
        <div class="container mx-auto px-4 text-center text-gray-600 dark:text-gray-400 text-sm">
            <p>© <span id="current-year"></span> AGV任务统计平台 | 数据报告生成系统</p>
            <p class="mt-1">本报告仅供内部使用，请勿外传</p>
        </div>
    </footer>

    <script>
        // 图表数据
        const chartData = {chart_data_json};
        let currentTheme = 'light';
        let filteredData = [...chartData.raw_data.data];
        
        // 初始化页面
        function initPage() {
            // 更新页面信息
            document.getElementById('time-range').textContent = 
                chartData.raw_data.begin_time + ' 至 ' + chartData.raw_data.end_time;
            document.getElementById('total-count').textContent = chartData.raw_data.total_count.toLocaleString();
            document.getElementById('active-robots').textContent = chartData.all_robots.length;
            document.getElementById('task-types').textContent = chartData.all_types.length;
            document.getElementById('group-count').textContent = chartData.all_groups.length;
            
            // 计算并显示各类型任务完成数量
            const nameStatusData = chartData.name_status_data;
            
            // 出库任务完成数量（O和ZCO类型）
            const outboundFinished = 
                                     (nameStatusData['ZCO']?.['FINISHED'] || 0) + 
                                     (nameStatusData['ZCO']?.['FORCE_FINISH'] || 0);
            document.getElementById('outbound-finished').textContent = outboundFinished.toLocaleString();
            
            // 入库任务完成数量
            const inboundFinished = (nameStatusData['I']?.['FINISHED'] || 0) + 
                                    (nameStatusData['I']?.['FORCE_FINISH'] || 0);
            document.getElementById('inbound-finished').textContent = inboundFinished.toLocaleString();
            
            // 移库任务完成数量
            const moveFinished = (nameStatusData['yk']?.['FINISHED'] || 0) + 
                                 (nameStatusData['yk']?.['FORCE_FINISH'] || 0);
            document.getElementById('move-finished').textContent = moveFinished.toLocaleString();
            
            // 其他任务完成数量
            const otherFinished = (nameStatusData['other']?.['FINISHED'] || 0) + 
                                 (nameStatusData['other']?.['FORCE_FINISH'] || 0);
            document.getElementById('other-finished').textContent = otherFinished.toLocaleString();
            
            // 设置生成时间
            const now = new Date();
            document.getElementById('generate-time').textContent = now.toLocaleString('zh-CN');
            document.getElementById('current-year').textContent = now.getFullYear();
            
            // 填充表格数据
            populateTable(filteredData);
            
            // 初始化图表
            initCharts();
            
            // 初始化主题
            initTheme();
            
            // 绑定事件
            bindEvents();
        }
        
        // 填充表格数据
        function populateTable(data) {
            const tableBody = document.getElementById('table-body');
            tableBody.innerHTML = '';
            
            data.forEach((item, index) => {
                const row = tableBody.insertRow();
                row.className = 'border-b border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors';
                
                const robotCell = row.insertCell(0);
                robotCell.className = 'py-3 px-4';
                robotCell.innerHTML = `<div class="font-medium">${item.robot_id}</div>`;
                
                const typeCell = row.insertCell(1);
                typeCell.className = 'py-3 px-4';
                typeCell.innerHTML = `<div class="text-gray-700 dark:text-gray-300">${item.type}</div>`;
                
                const statusCell = row.insertCell(2);
                statusCell.className = 'py-3 px-4';
                
                let statusClass = 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300';
                let statusIcon = 'fa-circle-o';
                
                switch(item.status.toLowerCase()) {
                    case 'finished':
                        statusClass = 'bg-success/10 text-success';
                        statusIcon = 'fa-check-circle';
                        break;
                    case 'canceled':
                        statusClass = 'bg-warning/10 text-warning';
                        statusIcon = 'fa-times-circle';
                        break;
                    case 'processing':
                        statusClass = 'bg-info/10 text-info';
                        statusIcon = 'fa-spinner';
                        break;
                    case 'force_finish':
                        statusClass = 'bg-danger/10 text-danger';
                        statusIcon = 'fa-exclamation-circle';
                        break;
                }
                
                statusCell.innerHTML = `
                    <div class="flex items-center">
                        <i class="fa ${statusIcon} mr-2"></i>
                        <span class="px-2 py-1 rounded-full text-xs font-medium ${statusClass}">${item.status}</span>
                    </div>
                `;
                
                const countCell = row.insertCell(3);
                countCell.className = 'py-3 px-4 text-right';
                countCell.innerHTML = `<div class="font-semibold text-lg">${item.count.toLocaleString()}</div>`;
                
                const actionCell = row.insertCell(4);
                actionCell.className = 'py-3 px-4 text-center';
                actionCell.innerHTML = `
                    <button class="view-details p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors" title="查看详情">
                        <i class="fa fa-eye text-gray-500"></i>
                    </button>
                `;
            });
            
            // 更新记录数
            document.getElementById('total-records').textContent = data.length.toLocaleString();
        }
        
        // 初始化图表
        function initCharts() {
            // 任务状态分布饼图
            const statusPieChart = echarts.init(document.getElementById('status-pie-chart'));
            const statusPieOption = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'item',
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#e2e8f0',
                    borderWidth: 1,
                    textStyle: {
                        color: '#333'
                    },
                    formatter: function(params) {
                        return `
                            <div class="py-1 px-2">
                                <div class="font-medium">${params.name}</div>
                                <div class="flex items-center justify-between mt-1">
                                    <span>数量:</span>
                                    <span class="font-semibold">${params.value}</span>
                                </div>
                                <div class="flex items-center justify-between mt-1">
                                    <span>占比:</span>
                                    <span class="font-semibold">${params.percent}%</span>
                                </div>
                            </div>
                        `;
                    }
                },
                legend: {
                    orient: 'vertical',
                    right: 10,
                    top: 'center',
                    textStyle: {
                        color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563',
                        fontSize: 12
                    },
                    icon: 'circle'
                },
                series: [
                    {
                        name: '任务状态',
                        type: 'pie',
                        radius: ['40%', '70%'],
                        center: ['35%', '50%'],
                        avoidLabelOverlap: false,
                        itemStyle: {
                            borderRadius: 8,
                            borderColor: currentTheme === 'dark' ? '#1e293b' : '#ffffff',
                            borderWidth: 2,
                            shadowBlur: 10,
                            shadowColor: 'rgba(0, 0, 0, 0.1)'
                        },
                        label: {
                            show: false,
                            position: 'center'
                        },
                        emphasis: {
                            label: {
                                show: true,
                                fontSize: '20',
                                fontWeight: 'bold',
                                color: currentTheme === 'dark' ? '#ffffff' : '#333333'
                            },
                            itemStyle: {
                                shadowBlur: 20,
                                shadowOffsetX: 0,
                                shadowColor: 'rgba(0, 0, 0, 0.5)'
                            }
                        },
                        labelLine: {
                            show: false
                        },
                        data: Object.entries(chartData.status_data).map(([key, value]) => ({
                            value: value,
                            name: key,
                            itemStyle: {
                                color: getStatusColor(key)
                            }
                        }))
                    }
                ]
            };
            statusPieChart.setOption(statusPieOption);
            
            // 各机器人任务数量对比图表
            renderRobotChart('bar');
            
            // 渲染各任务类型状态分布堆叠柱状图
            renderTypeStatusStackChart();
            
            // 渲染各组状态分布堆叠柱状图
            renderGroupStatusStackChart();
            
            // 渲染各名称状态分布堆叠柱状图
            renderNameStatusStackChart();
            
            // 渲染各机器人状态分布堆叠柱状图
            renderRobotStatusStackChart();
        }
        
        // 渲染任务类型状态分布堆叠柱状图
        function renderTypeStatusStackChart() {
            const typeStatusStackChart = echarts.init(document.getElementById('type-status-stack-chart'));
            
            // 准备堆叠柱状图数据
            const stackTaskTypes = Object.keys(chartData.type_status_data);
            const statuses = chartData.all_statuses;
            const stackSeries = statuses.map(status => {
                const data = stackTaskTypes.map(type => 
                    chartData.type_status_data[type] && chartData.type_status_data[type][status] 
                        ? chartData.type_status_data[type][status] 
                        : 0
                );
                return {
                    name: status,
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: data,
                    itemStyle: {
                        color: getStatusColor(status)
                    }
                };
            });
            
            const stackOption = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        type: 'shadow'
                    },
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#e2e8f0',
                    borderWidth: 1,
                    textStyle: {
                        color: '#333'
                    }
                },
                legend: {
                    data: statuses,
                    bottom: 20,
                    textStyle: {
                        color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563'
                    },
                    icon: 'circle',
                    itemWidth: 8,
                    itemHeight: 8
                },
                grid: {
                    left: '3%',
                    right: '4%',
                    top: '3%',
                    bottom: '15%',
                    containLabel: true
                },
                xAxis: {
                    type: 'category',
                    data: stackTaskTypes,
                    axisLabel: {
                        interval: 0,
                        rotate: 30,
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
                    },
                    axisLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                        }
                    },
                    axisTick: {
                        show: false
                    }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                        formatter: function(value) {
                            return value >= 1000 ? (value / 1000) + 'k' : value;
                        }
                    },
                    axisLine: {
                        show: false
                    },
                    axisTick: {
                        show: false
                    },
                    splitLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                            type: 'dashed'
                        }
                    }
                },
                series: stackSeries
            };
            typeStatusStackChart.setOption(stackOption);
            return typeStatusStackChart;
        }
        
        // 渲染各组状态分布堆叠柱状图
        function renderGroupStatusStackChart() {
            const groupStatusStackChart = echarts.init(document.getElementById('group-status-stack-chart'));
            
            // 准备堆叠柱状图数据
            const groups = chartData.all_groups;
            const statuses = chartData.all_statuses;
            const stackSeries = statuses.map(status => {
                const data = groups.map(group => 
                    chartData.group_status_data[group] && chartData.group_status_data[group][status] 
                        ? chartData.group_status_data[group][status] 
                        : 0
                );
                return {
                    name: status,
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: data,
                    itemStyle: {
                        color: getStatusColor(status)
                    }
                };
            });
            
            const stackOption = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        type: 'shadow'
                    },
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#e2e8f0',
                    borderWidth: 1,
                    textStyle: {
                        color: '#333'
                    }
                },
                legend: {
                    data: statuses,
                    bottom: 20,
                    textStyle: {
                        color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563'
                    },
                    icon: 'circle',
                    itemWidth: 8,
                    itemHeight: 8
                },
                grid: {
                    left: '3%',
                    right: '4%',
                    top: '3%',
                    bottom: '15%',
                    containLabel: true
                },
                xAxis: {
                    type: 'category',
                    data: groups,
                    axisLabel: {
                        interval: 0,
                        rotate: 30,
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
                    },
                    axisLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                        }
                    },
                    axisTick: {
                        show: false
                    }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                        formatter: function(value) {
                            return value >= 1000 ? (value / 1000) + 'k' : value;
                        }
                    },
                    axisLine: {
                        show: false
                    },
                    axisTick: {
                        show: false
                    },
                    splitLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                            type: 'dashed'
                        }
                    }
                },
                series: stackSeries
            };
            groupStatusStackChart.setOption(stackOption);
            return groupStatusStackChart;
            
            // 响应式处理
            window.addEventListener('resize', function() {
                statusPieChart.resize();
                const robotChart = echarts.getInstanceByDom(document.getElementById('robot-bar-chart'));
                if (robotChart) robotChart.resize();
                const typeChart = echarts.getInstanceByDom(document.getElementById('type-status-stack-chart'));
                if (typeChart) typeChart.resize();
                const groupChart = echarts.getInstanceByDom(document.getElementById('group-status-stack-chart'));
                if (groupChart) groupChart.resize();
                const nameChart = echarts.getInstanceByDom(document.getElementById('name-status-stack-chart'));
                if (nameChart) nameChart.resize();
                const robotStatusChart = echarts.getInstanceByDom(document.getElementById('robot-status-stack-chart'));
                if (robotStatusChart) robotStatusChart.resize();
            });
        }
        
        // 渲染名称状态分布堆叠柱状图
            function renderNameStatusStackChart() {
                const nameStatusStackChart = echarts.init(document.getElementById('name-status-stack-chart'));
                
                // 准备堆叠柱状图数据
                const names = Object.keys(chartData.name_status_data);
                const statuses = chartData.all_statuses;
                
                // 映射名称到中文标签
                const nameToLabel = {
                    'I': '入库',
                    'O': '出库',
                    'yk': '移库',
                    'ZCO': '出库',
                    'other': '其他'
                };
                
                // 确保X轴按顺序显示：入库、出库、移库、其他
                const orderedLabels = ['入库', '出库', '移库', '其他'];
                const orderedNames = ['I', 'ZCO', 'yk', 'other'];
                
                // 过滤出存在的数据
                const filteredOrderedNames = orderedNames.filter(name => names.includes(name));
                const filteredOrderedLabels = filteredOrderedNames.map(name => nameToLabel[name]);
                
                const stackSeries = statuses.map(status => {
                    const data = filteredOrderedNames.map(name => 
                        chartData.name_status_data[name] && chartData.name_status_data[name][status] 
                            ? chartData.name_status_data[name][status] 
                            : 0
                    );
                    return {
                        name: status,
                        type: 'bar',
                        stack: 'total',
                        emphasis: {
                            focus: 'series'
                        },
                        data: data,
                        itemStyle: {
                            color: getStatusColor(status)
                        },
                        label: {
                            show: true,
                            position: 'inside',
                            formatter: function(params) {
                                return params.value > 0 ? params.value : '';
                            },
                            color: '#fff',
                            fontSize: 12,
                            fontWeight: 'bold'
                        }
                    };
                });
                
                const stackOption = {
                    backgroundColor: 'transparent',
                    tooltip: {
                        trigger: 'axis',
                        axisPointer: {
                            type: 'shadow'
                        },
                        backgroundColor: 'rgba(255, 255, 255, 0.95)',
                        borderColor: '#e2e8f0',
                        borderWidth: 1,
                        textStyle: {
                            color: '#333'
                        }
                    },
                    legend: {
                        data: statuses,
                        bottom: 20,
                        textStyle: {
                            color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563'
                        },
                        icon: 'circle',
                        itemWidth: 8,
                        itemHeight: 8
                    },
                    grid: {
                        left: '3%',
                        right: '4%',
                        top: '3%',
                        bottom: '15%',
                        containLabel: true
                    },
                    xAxis: {
                        type: 'category',
                        data: filteredOrderedLabels,
                    axisLabel: {
                        interval: 0,
                        rotate: 30,
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
                    },
                    axisLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                        }
                    },
                    axisTick: {
                        show: false
                    }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                        formatter: function(value) {
                            return value >= 1000 ? (value / 1000) + 'k' : value;
                        }
                    },
                    axisLine: {
                        show: false
                    },
                    axisTick: {
                        show: false
                    },
                    splitLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                            type: 'dashed'
                        }
                    }
                },
                series: stackSeries
            };
            // 添加图例点击事件，显示对应状态的原始数据
            nameStatusStackChart.on('legendselectchanged', function(params) {
                const selectedStatus = Object.keys(params.selected).find(key => params.selected[key]);
                if (selectedStatus) {
                    // 过滤出该状态的原始数据
                    const statusRawData = chartData.raw_data.data.filter(item => item.status === selectedStatus);
                    // 创建并显示提示框展示原始数据
                    showRawDataDialog(selectedStatus, statusRawData);
                }
            });
            
            nameStatusStackChart.setOption(stackOption);
            return nameStatusStackChart;
        }
        
        // 显示原始数据对话框
        function showRawDataDialog(status, rawData) {
            // 创建临时对话框元素
            const dialog = document.createElement('div');
            dialog.className = 'fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4';
            dialog.style.fontSize = '14px';
            
            // 对话框内容
            const dialogContent = document.createElement('div');
            dialogContent.className = `bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full max-h-[80vh] overflow-hidden flex flex-col`;
            
            // 对话框标题
            const dialogHeader = document.createElement('div');
            dialogHeader.className = 'px-6 py-4 border-b dark:border-gray-700 flex justify-between items-center';
            dialogHeader.innerHTML = `
                <h3 class="text-lg font-semibold dark:text-white">状态 "${status}" 的原始数据</h3>
                <button id="close-dialog" class="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-white">
                    <i class="fa fa-times text-xl"></i>
                </button>
            `;
            
            // 数据表格容器
            const tableContainer = document.createElement('div');
            tableContainer.className = 'overflow-auto flex-grow p-4';
            
            // 创建表格
            const table = document.createElement('table');
            table.className = 'min-w-full divide-y divide-gray-200 dark:divide-gray-700';
            
            // 表头
            const thead = document.createElement('thead');
            thead.innerHTML = `
                <tr>
                    <th class="px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">机器人ID</th>
                    <th class="px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">任务类型</th>
                    <th class="px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">名称</th>
                    <th class="px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">组ID</th>
                    <th class="px-4 py-3 bg-gray-50 dark:bg-gray-800 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">数量</th>
                </tr>
            `;
            
            // 表格内容
            const tbody = document.createElement('tbody');
            tbody.className = 'bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700';
            
            if (rawData.length > 0) {
                rawData.forEach(item => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">${item.robot_id || '-'}</td>
                        <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">${item.type || '-'}</td>
                        <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">${item.name || '-'}</td>
                        <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">${item.group_id || '-'}</td>
                        <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">${item.count || 0}</td>
                    `;
                    tbody.appendChild(row);
                });
            } else {
                const emptyRow = document.createElement('tr');
                emptyRow.innerHTML = `
                    <td colspan="5" class="px-4 py-6 text-center text-sm text-gray-500 dark:text-gray-400">没有找到相关数据</td>
                `;
                tbody.appendChild(emptyRow);
            }
            
            table.appendChild(thead);
            table.appendChild(tbody);
            tableContainer.appendChild(table);
            
            // 添加到对话框内容
            dialogContent.appendChild(dialogHeader);
            dialogContent.appendChild(tableContainer);
            dialog.appendChild(dialogContent);
            
            // 添加到文档
            document.body.appendChild(dialog);
            
            // 关闭对话框
            dialog.querySelector('#close-dialog').addEventListener('click', function() {
                document.body.removeChild(dialog);
            });
            
            // 点击对话框外部关闭
            dialog.addEventListener('click', function(e) {
                if (e.target === dialog) {
                    document.body.removeChild(dialog);
                }
            });
        }
        
        // 渲染机器人状态分布堆叠柱状图
        function renderRobotStatusStackChart() {
            const robotStatusStackChart = echarts.init(document.getElementById('robot-status-stack-chart'));
            
            // 准备堆叠柱状图数据
            const robots = Object.keys(chartData.robot_status_data);
            const statuses = chartData.all_statuses;
            const stackSeries = statuses.map(status => {
                const data = robots.map(robot => 
                    chartData.robot_status_data[robot] && chartData.robot_status_data[robot][status] 
                        ? chartData.robot_status_data[robot][status] 
                        : 0
                );
                return {
                    name: status,
                    type: 'bar',
                    stack: 'total',
                    emphasis: {
                        focus: 'series'
                    },
                    data: data,
                    itemStyle: {
                        color: getStatusColor(status)
                    }
                };
            });
            
            const stackOption = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        type: 'shadow'
                    },
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#e2e8f0',
                    borderWidth: 1,
                    textStyle: {
                        color: '#333'
                    }
                },
                legend: {
                    data: statuses,
                    bottom: 20,
                    textStyle: {
                        color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563'
                    },
                    icon: 'circle',
                    itemWidth: 8,
                    itemHeight: 8
                },
                grid: {
                    left: '3%',
                    right: '4%',
                    top: '3%',
                    bottom: '15%',
                    containLabel: true
                },
                xAxis: {
                    type: 'category',
                    data: robots,
                    axisLabel: {
                        interval: 0,
                        rotate: 30,
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
                    },
                    axisLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                        }
                    },
                    axisTick: {
                        show: false
                    }
                },
                yAxis: {
                    type: 'value',
                    axisLabel: {
                        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                        formatter: function(value) {
                            return value >= 1000 ? (value / 1000) + 'k' : value;
                        }
                    },
                    axisLine: {
                        show: false
                    },
                    axisTick: {
                        show: false
                    },
                    splitLine: {
                        lineStyle: {
                            color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                            type: 'dashed'
                        }
                    }
                },
                series: stackSeries
            };
            robotStatusStackChart.setOption(stackOption);
            return robotStatusStackChart;
        }
        
        // 渲染机器人图表
        function renderRobotChart(type) {
            const robotChartDom = document.getElementById('robot-bar-chart');
            const robotChart = echarts.getInstanceByDom(robotChartDom);
            if (robotChart) {
                robotChart.dispose();
            }
            const newRobotChart = echarts.init(robotChartDom);
            
            // 准备数据
            const robotNames = chartData.all_robots;
            const taskTypes = chartData.all_types;
            const series = taskTypes.map(taskType => {
                const data = robotNames.map(robot => 
                    chartData.robot_data[robot] && chartData.robot_data[robot][taskType] 
                        ? chartData.robot_data[robot][taskType] 
                        : 0
                );
                
                return {
                    name: taskType,
                    type: type,
                    data: data,
                    smooth: type === 'line',
                    symbol: type === 'radar' ? 'circle' : 'emptyCircle',
                    symbolSize: 6,
                    itemStyle: {
                        borderRadius: type === 'bar' ? [4, 4, 0, 0] : undefined
                    }
                };
            });
            
            const commonOption = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        type: type === 'radar' ? 'cross' : 'shadow'
                    },
                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                    borderColor: '#e2e8f0',
                    borderWidth: 1,
                    textStyle: {
                        color: '#333'
                    }
                },
                legend: {
                    data: taskTypes,
                    bottom: 0,
                    textStyle: {
                        color: currentTheme === 'dark' ? '#e2e8f0' : '#4b5563'
                    },
                    icon: 'circle',
                    itemWidth: 8,
                    itemHeight: 8
                },
                series: series
            };
            
            let option;
            
            if (type === 'radar') {
                option = {
                    ...commonOption,
                    radar: {
                        indicator: robotNames.map(robot => ({ name: robot, max: Math.max(...taskTypes.map(taskType => 
                            chartData.robot_data[robot] && chartData.robot_data[robot][taskType] ? 
                            chartData.robot_data[robot][taskType] : 0
                        )) * 1.2 })),
                        shape: 'circle',
                        splitNumber: 5,
                        axisName: {
                            color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                            fontSize: 10
                        },
                        splitLine: {
                            lineStyle: {
                                color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                                type: 'dashed'
                            }
                        },
                        splitArea: {
                            show: true,
                            areaStyle: {
                                color: currentTheme === 'dark' 
                                    ? ['rgba(30, 41, 59, 0.3)', 'rgba(30, 41, 59, 0.5)']
                                    : ['rgba(255, 255, 255, 0.9)', 'rgba(241, 245, 249, 0.9)']
                            }
                        },
                        axisLine: {
                            lineStyle: {
                                color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                            }
                        }
                    }
                };
            } else {
                option = {
                    ...commonOption,
                    grid: {
                        left: '3%',
                        right: '4%',
                        bottom: '15%',
                        containLabel: true
                    },
                    xAxis: {
                        type: 'category',
                        data: robotNames,
                        axisLabel: {
                            interval: 0,
                            rotate: 45,
                            color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
                        },
                        axisLine: {
                            lineStyle: {
                                color: currentTheme === 'dark' ? '#475569' : '#e2e8f0'
                            }
                        },
                        axisTick: {
                            show: false
                        }
                    },
                    yAxis: {
                        type: 'value',
                        axisLabel: {
                            color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
                            formatter: function(value) {
                                return value >= 1000 ? (value / 1000) + 'k' : value;
                            }
                        },
                        axisLine: {
                            show: false
                        },
                        axisTick: {
                            show: false
                        },
                        splitLine: {
                            lineStyle: {
                                color: currentTheme === 'dark' ? '#334155' : '#f1f5f9',
                                type: 'dashed'
                            }
                        }
                    }
                };
            }
            
            newRobotChart.setOption(option);
        }
        
        // 获取状态对应的颜色
        function getStatusColor(status) {
            const colors = {
                'finished': '#10b981',
                'canceled': '#f59e0b',
                'processing': '#3b82f6',
                'force_finish': '#ef4444',
                'default': '#6b7280'
            };
            
            return colors[status.toLowerCase()] || colors.default;
        }
        
        // 初始化主题
        function initTheme() {
            // 检查系统主题偏好
            if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
                document.documentElement.classList.add('dark');
                currentTheme = 'dark';
            }
        }
        
        // 切换主题
        function toggleTheme() {
            document.documentElement.classList.toggle('dark');
            currentTheme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
            
            // 重新初始化图表以更新主题
            initCharts();
        }
        
        // 绑定事件
        function bindEvents() {
            // 主题切换按钮
            document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
            
            // 刷新图表按钮
            document.getElementById('refresh-charts').addEventListener('click', function() {
                this.classList.add('animate-spin');
                setTimeout(() => {
                    this.classList.remove('animate-spin');
                    initCharts();
                }, 500);
            });
            
            // 机器人图表类型切换
            document.getElementById('robot-chart-type').addEventListener('change', function() {
                renderRobotChart(this.value);
            });
            
            // 数据搜索
            document.getElementById('data-search').addEventListener('input', function() {
                const searchTerm = this.value.toLowerCase().trim();
                filteredData = chartData.raw_data.data.filter(item => 
                    item.robot_id.toLowerCase().includes(searchTerm) ||
                    item.type.toLowerCase().includes(searchTerm) ||
                    item.status.toLowerCase().includes(searchTerm)
                );
                populateTable(filteredData);
            });
            
            // 导出表格数据
            document.getElementById('export-table').addEventListener('click', function() {
                // 简单实现：将表格数据转换为JSON格式下载
                const dataStr = JSON.stringify(filteredData, null, 2);
                const dataBlob = new Blob([dataStr], { type: 'application/json' });
                const url = URL.createObjectURL(dataBlob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `agv_task_data_${new Date().toISOString().split('T')[0]}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            });
            
            // 状态筛选按钮
            document.querySelectorAll('.status-filter-btn').forEach(btn => {
                btn.addEventListener('click', function() {
                    document.querySelectorAll('.status-filter-btn').forEach(b => {
                        b.classList.remove('active', 'bg-primary/10', 'text-primary');
                        b.classList.add('bg-gray-100', 'dark:bg-gray-800', 'text-gray-600', 'dark:text-gray-300');
                    });
                    
                    this.classList.add('active', 'bg-primary/10', 'text-primary');
                    this.classList.remove('bg-gray-100', 'dark:bg-gray-800', 'text-gray-600', 'dark:text-gray-300');
                    
                    // 这里可以根据筛选条件更新图表和表格
                    // 简单实现：重置数据
                    initCharts();
                });
            });
            
            // 展开/收起任务类型状态分布图表
            document.getElementById('type-stack-chart-expand').addEventListener('click', function() {
                const chartContainer = document.getElementById('type-status-stack-chart');
                const isExpanded = chartContainer.style.height === '600px';
                chartContainer.style.height = isExpanded ? '450px' : '600px';
                this.innerHTML = isExpanded ? '<i class="fa fa-expand"></i>' : '<i class="fa fa-compress"></i>';
                
                // 重新调整图表大小
                const chart = echarts.getInstanceByDom(chartContainer);
                if (chart) chart.resize();
            });
            
            // 展开/收起各组状态分布图表
            document.getElementById('group-stack-chart-expand').addEventListener('click', function() {
                const chartContainer = document.getElementById('group-status-stack-chart');
                const isExpanded = chartContainer.style.height === '600px';
                chartContainer.style.height = isExpanded ? '450px' : '600px';
                this.innerHTML = isExpanded ? '<i class="fa fa-expand"></i>' : '<i class="fa fa-compress"></i>';
                
                // 重新调整图表大小
                const chart = echarts.getInstanceByDom(chartContainer);
                if (chart) chart.resize();
            });
            
            // 展开/收起名称状态分布图表
            document.getElementById('name-stack-chart-expand').addEventListener('click', function() {
                const chartContainer = document.getElementById('name-status-stack-chart');
                const isExpanded = chartContainer.style.height === '600px';
                chartContainer.style.height = isExpanded ? '450px' : '600px';
                this.innerHTML = isExpanded ? '<i class="fa fa-expand"></i>' : '<i class="fa fa-compress"></i>';
                
                // 重新调整图表大小
                const chart = echarts.getInstanceByDom(chartContainer);
                if (chart) chart.resize();
            });
            
            // 展开/收起机器人状态分布图表
            document.getElementById('robot-stack-chart-expand').addEventListener('click', function() {
                const chartContainer = document.getElementById('robot-status-stack-chart');
                const isExpanded = chartContainer.style.height === '600px';
                chartContainer.style.height = isExpanded ? '450px' : '600px';
                this.innerHTML = isExpanded ? '<i class="fa fa-expand"></i>' : '<i class="fa fa-compress"></i>';
                
                // 重新调整图表大小
                const chart = echarts.getInstanceByDom(chartContainer);
                if (chart) chart.resize();
            });
        }
        
        // 表格排序功能
        function sortTable(columnIndex) {
            const tableBody = document.getElementById('table-body');
            const rows = Array.from(tableBody.rows);
            
            // 检查当前排序状态
            const th = tableBody.parentNode.tHead.rows[0].cells[columnIndex];
            const sortIcon = th.querySelector('i');
            let sortDirection = sortIcon.className.includes('fa-sort-desc') ? 'desc' : 'asc';
            
            // 更新所有排序图标
            document.querySelectorAll('th i').forEach(icon => {
                icon.className = 'fa fa-sort ml-1';
            });
            
            // 设置当前列的排序图标
            sortIcon.className = sortDirection === 'asc' ? 'fa fa-sort-asc ml-1' : 'fa fa-sort-desc ml-1';
            
            // 排序
            rows.sort((a, b) => {
                let aValue, bValue;
                
                if (columnIndex === 3) { // 数量列
                    aValue = parseInt(a.cells[columnIndex].textContent.trim());
                    bValue = parseInt(b.cells[columnIndex].textContent.trim());
                } else {
                    aValue = a.cells[columnIndex].textContent.trim().toLowerCase();
                    bValue = b.cells[columnIndex].textContent.trim().toLowerCase();
                }
                
                if (sortDirection === 'asc') {
                    return aValue > bValue ? 1 : -1;
                } else {
                    return aValue < bValue ? 1 : -1;
                }
            });
            
            // 重新添加行
            rows.forEach(row => tableBody.appendChild(row));
        }
        
        // 执行初始化
        document.addEventListener('DOMContentLoaded', initPage);
    </script>
</body>
</html>
        '''
        
        # 替换数据占位符
        html_content = html_content.replace('{chart_data_json}', chart_data_json)
        
        # 写入文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"HTML报告生成完成: {output_path}")
        return output_path
    
    except Exception as e:
        print(f"生成HTML报告失败: {str(e)}")
        raise

