import json
import traceback
from typing import Dict, Any, List
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker, aliased
from datetime import datetime
from app.modules.admin.models_das.models import RealtimeData, HistoryData, CollectionData
from app.modules.admin.utils_das.config import DATABASE_URL

def iso_to_timestamp_ms(dt_str):
    if dt_str:
        try:
            if dt_str.endswith('Z'): 
                dt = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M:%S.%fZ')
            else:
                dt = datetime.fromisoformat(dt_str)
            return int(dt.timestamp())
        except (ValueError, TypeError) as e:
            print(f"转换时间戳失败: {e}")
            return int(datetime.now().timestamp() * 1000)
    return int(datetime.now().timestamp() * 1000)

class DataService:
    engine = create_engine(DATABASE_URL,echo=False)
    
    @classmethod
    def get_session(cls):
        Session = sessionmaker(bind=cls.engine)
        return Session()
    
    @staticmethod
    def insert_batch_collection_data(batch_data):
        session = None
        try:
            session = DataService.get_session()
            
            record_time = datetime.now().isoformat()
            
            result_ids = []
            
            for item in batch_data:
                project = item['project']
                indicator = item['indicator']
                data = item['data']
                collection_time_str = item.get('collection_time')
                start_time_str = item['start_time']
                end_time_str = item['end_time']
                
                collection_time_iso = collection_time_str if collection_time_str else record_time
                
                start_time_int = iso_to_timestamp_ms(start_time_str)
                end_time_int = iso_to_timestamp_ms(end_time_str)

                print(f"[{start_time_str}-{end_time_str}] 转换时间: start_time_str={start_time_str}, end_time_str={end_time_str}, start_time_int={start_time_int}, end_time_int={end_time_int}")
                
                if isinstance(data, (dict, list)):
                    data_str = json.dumps(data)
                else:
                    data_str = str(data)
                
                existing_record = session.query(CollectionData).filter(
                    CollectionData.project == project,
                    CollectionData.indicator == indicator,
                    CollectionData.start_time_int == start_time_int,
                    CollectionData.end_time_int == end_time_int
                ).first()
                
                if existing_record:
                    existing_record.data = data_str
                    existing_record.collection_time = collection_time_iso
                    existing_record.record_time = record_time
                    result_ids.append(existing_record.id)
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 批量更新采集数据成功: 项目={project}, 指标={indicator}, ID={existing_record.id}")
                else:
                    new_record = CollectionData(
                        project=project,
                        indicator=indicator,
                        data=data_str,
                        collection_time=collection_time_iso,
                        record_time=record_time,
                        time_str=f'{start_time_str}|{end_time_str}',
                        start_time_int=start_time_int,
                        end_time_int=end_time_int
                    )
                    session.add(new_record)
                    session.flush()
                    result_ids.append(new_record.id)
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 批量插入采集数据成功: 项目={project}, 指标={indicator}, ID={new_record.id}")
            
            session.commit()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 批量处理完成，共处理 {len(batch_data)} 条记录")
            return True, result_ids
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 批量处理采集数据失败: {traceback.format_exc()}")
            if session:
                session.rollback()
            return False, None
        finally:
            if session:
                session.close()
    
    @staticmethod
    def get_collection_time(project: str, tag: str) -> str:
        session = None
        try:
            session = DataService.get_session()
            
            record = session.query(CollectionData).filter(
                CollectionData.project == project,
                CollectionData.indicator == tag
            ).order_by(desc(CollectionData.start_time_int)).first()
            
            return record.collection_time if record else None
        except Exception as e:
            print(f"获取collection_time失败: {e}")
            return None
        finally:
            if session:
                session.close()

    @staticmethod
    def get_collection_data_for_indicators(project: str, tag: str, indicators: List[str], start_time: str = '', end_time: str = '') -> List[Dict[str, Any]]:
        result_item = {
            'project': None,
            'tag': None,
            'authorized_indicators': None,
            'content': [],
            'collection_time':None,
            'record_time': None
        }
        session = None
        
        try:
            session = DataService.get_session()
            
            CD1 = aliased(CollectionData)
            CD2 = aliased(CollectionData)
            
            subquery = session.query(CD2.id).filter(
                CD2.project == project,
                CD2.indicator == tag
            )
            
            if start_time:
                start_time_ms = iso_to_timestamp_ms(start_time)
                subquery = subquery.filter(CD2.start_time_int >= start_time_ms)
            if end_time:
                end_time_ms = iso_to_timestamp_ms(end_time)
                subquery = subquery.filter(CD2.end_time_int <= end_time_ms)
            
            subquery = subquery.order_by(CD2.start_time_int)
            
            subquery_obj = subquery.subquery()
            
            print(f"查询条件: project={project}, tag={tag}, start_time={start_time}, end_time={end_time}")
            query = session.query(CD1).join(
                subquery_obj,
                CD1.id == subquery_obj.c.id
            )
            
            rows = query.all()
            

            for row in rows:
                try:
                    data_obj = json.loads(row.data)
                except json.JSONDecodeError:
                    data_obj = row.data

                data_new = {}
                if '*' in indicators:   
                    data_new = data_obj
                else:
                    for indicator in indicators:
                        if indicator in data_obj:
                            data_new[indicator] = data_obj.get(indicator, None)  

                result_item['project'] = row.project
                result_item['tag'] = tag   
                result_item['authorized_indicators'] = indicators
                result_item['content'].append(data_new) 
                result_item['collection_time'] = row.collection_time
                result_item['record_time'] = row.record_time
        except Exception as e:
            print(f"数据库查询错误: {str(e)}")
            result_item['message'] = str(e)
        finally:
            if session:
                session.close()
            
        return result_item