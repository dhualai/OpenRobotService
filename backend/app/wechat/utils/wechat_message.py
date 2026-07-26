import time
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def parse_wechat_xml(xml_data: bytes) -> Dict[str, str]:
    try:
        logger.info(f"开始解析微信XML消息，数据长度: {len(xml_data)} 字节")
        logger.debug(f"原始XML数据: {xml_data}")
        
        root = ET.fromstring(xml_data)
        message = {}
        
        for child in root:
            message[child.tag] = child.text
        
        logger.info(f"成功解析XML消息，字段数: {len(message)}")
        logger.debug(f"解析后的消息内容: {message}")
        
        return message
    except Exception as e:
        logger.error(f"解析微信XML消息失败: {e}", exc_info=True)
        raise


def build_reply_text(to_user: str, from_user: str, content: str) -> str:
    now = str(int(time.time()))
    reply = f'''
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{now}</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[{content}]]></Content>
    </xml>
    '''
    return reply


def build_reply_news(to_user: str, from_user: str, articles: List[Dict[str, str]]) -> str:
    now = str(int(time.time()))
    article_count = len(articles)
    
    articles_xml = ""
    for article in articles:
        articles_xml += f'''
        <item>
            <Title><![CDATA[{article['title']}]]></Title>
            <Description><![CDATA[{article['description']}]]></Description>
            <PicUrl><![CDATA[{article['picurl']}]]></PicUrl>
            <Url><![CDATA[{article['url']}]]></Url>
        </item>'''
    
    reply = f'''
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{now}</CreateTime>
        <MsgType><![CDATA[news]]></MsgType>
        <ArticleCount>{article_count}</ArticleCount>
        <Articles>{articles_xml}
        </Articles>
    </xml>
    '''
    return reply


def build_reply_image(to_user: str, from_user: str, media_id: str) -> str:
    now = str(int(time.time()))
    reply = f'''
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{now}</CreateTime>
        <MsgType><![CDATA[image]]></MsgType>
        <Image>
            <MediaId><![CDATA[{media_id}]]></MediaId>
        </Image>
    </xml>
    '''
    return reply