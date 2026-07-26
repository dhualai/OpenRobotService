import re


def parse_daily_report(text):
    text = text.strip()
    lines = text.split('\n')
    
    result = {
        'project': None,
        'date': None,
        'content': None
    }
    
    if lines:
        first_line = lines[0].strip()
        project_match = re.match(r'^(日报|项目)[:：]?\s*(.+)$', first_line)
        if project_match:
            result['project'] = project_match.group(2).strip()
        else:
            result['project'] = first_line
    
    if len(lines) > 1:
        second_line = lines[1].strip()
        date_match = re.match(r'^(\d{4}年\d{1,2}月\d{1,2}日)$', second_line)
        if date_match:
            result['date'] = date_match.group(1)
    
    if len(lines) > 2:
        content_lines = lines[2:]
        content_lines = [line for line in content_lines if line.strip()]
        result['content'] = '\n'.join(content_lines)
    
    return result


def parse_daily_report_detailed(text):
    text = text.strip()
    lines = text.split('\n')
    
    result = {
        'project': None,
        'date': None,
        'today_work': [],
        'problems': [],
        'tomorrow_plan': []
    }
    
    if lines:
        first_line = lines[0].strip()
        project_match = re.match(r'^(日报|项目)[:：]?\s*(.+)$', first_line)
        if project_match:
            result['project'] = project_match.group(2).strip()
        else:
            result['project'] = first_line
    
    if len(lines) > 1:
        second_line = lines[1].strip()
        date_match = re.match(r'^(\d{4}年\d{1,2}月\d{1,2}日)$', second_line)
        if date_match:
            result['date'] = date_match.group(1)
    
    current_section = None
    current_problem = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if re.match(r'[一二三四五六七八九十]+、', line):
            if '今日工作情况' in line:
                current_section = 'today'
            elif '明日工作计划' in line:
                current_section = 'tomorrow'
            else:
                current_section = None
            continue
        
        if current_section == 'today':
            task_match = re.match(r'(\d+)\.(.+)', line)
            if task_match:
                result['today_work'].append({
                    'index': task_match.group(1),
                    'description': task_match.group(2).strip()
                })
            elif '问题项' in line:
                problem = line.replace('问题项:', '').replace('问题项：', '').strip()
                if problem:
                    current_problem = problem
            elif current_problem and not line.startswith('二、'):
                current_problem += ' ' + line
            elif current_problem and line.startswith('二、'):
                result['problems'].append(current_problem)
                current_problem = None
        
        elif current_section == 'tomorrow':
            task_match = re.match(r'(\d+)\.(.+)', line)
            if task_match:
                result['tomorrow_plan'].append({
                    'index': task_match.group(1),
                    'description': task_match.group(2).strip()
                })
    
    if current_problem:
        result['problems'].append(current_problem)
    
    return result