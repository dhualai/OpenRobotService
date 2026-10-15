from datetime import date, datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import MetaData
from sqlalchemy.engine.create import create_engine

from app.models.wecom_sheet import ExternalRecord
from app.modules.admin.api.auth import get_current_active_user_from_token
from app.modules.admin.api.dashboard import dashboard_router, get_db
from app.modules.admin.services.question_dashboard_service import (
    REPORT_TIME_FIELD, SOURCE_KEY, get_questions, parse_report_time,
)


class _SqliteDb:
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, query):
        return self.connection.execute(query)


class TestQuestionDashboard(IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        metadata = MetaData()
        self.table = ExternalRecord.__table__.to_metadata(metadata)
        metadata.create_all(self.engine)
        self.connection = self.engine.connect()
        self.db = _SqliteDb(self.connection)

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def insert(self, record_id, values, source_key=SOURCE_KEY):
        self.connection.execute(self.table.insert().values(
            source_key=source_key, record_id=record_id, values=values,
            raw_hash='test', pulled_at=datetime(2026, 10, 13, 1),
            created_at=datetime(2026, 10, 13, 1),
        ))

    async def test_real_column_names_and_ampm_without_zero_padding(self):
        self.insert('morning', {REPORT_TIME_FIELD: '2026/9/8 12:05 AM'})
        self.insert('afternoon', {
            REPORT_TIME_FIELD: '2026/9/8 2:54 PM',
            '问题描述': '任务异常\n第二行', '问题解决描述': '已调整参数',
            '车辆编号和车型': '00123 / XP3', '联系方式': 'private-data',
        })
        result = await get_questions(self.db, date(2026, 9, 8))
        self.assertEqual(result['total'], 2)
        self.assertEqual([item['record_id'] for item in result['items']], ['afternoon', 'morning'])
        item = result['items'][0]
        self.assertEqual(item['reported_at'], '2026-09-08T14:54:00+08:00')
        self.assertEqual(item['vehicle_info'], '00123 / XP3')
        self.assertEqual(item['problem_description'], '任务异常\n第二行')
        self.assertEqual(item['solution_description'], '已调整参数')
        self.assertNotIn('private-data', str(result))
        self.assertEqual(result['last_sync_at'], '2026-10-13T01:00:00+00:00')

    async def test_beijing_day_boundaries_and_source_isolation(self):
        for record_id, value in [
            ('start', '2026-10-12T16:00:00Z'),
            ('last', '2026-10-13T15:59:59Z'),
            ('previous', '2026-10-12T15:59:59Z'),
            ('next', '2026-10-13T16:00:00Z'),
            ('offset', '2026-10-14T00:30:00+14:00'),
        ]:
            self.insert(record_id, {REPORT_TIME_FIELD: value})
        self.insert('different-source', {REPORT_TIME_FIELD: '2026-10-13'}, 'other')
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual([item['record_id'] for item in result['items']], ['last', 'offset', 'start'])

    async def test_timestamp_seconds_milliseconds_and_date_only(self):
        self.insert('seconds', {REPORT_TIME_FIELD: 1791820800})
        self.insert('milliseconds', {REPORT_TIME_FIELD: '1791820800000'})
        self.insert('date-only', {'上报时间': '2026/10/13', '车辆编号': '00123', '车型': 'XP3'})
        self.insert('next-day', {REPORT_TIME_FIELD: 1791907200000})
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual(result['total'], 3)
        item = next(item for item in result['items'] if item['record_id'] == 'date-only')
        self.assertEqual(item['reported_at'], '2026-10-13')
        self.assertEqual(item['vehicle_number'], '00123')
        self.assertEqual(item['vehicle_model'], 'XP3')

    async def test_filter_before_pagination_without_thousand_record_cap(self):
        for index in range(1001):
            self.insert(f'other-day-{index}', {REPORT_TIME_FIELD: '2026/9/1 5:00 PM'})
        for index in range(25):
            self.insert(f'question-{index}', {REPORT_TIME_FIELD: '2026/10/13 5:00 PM'})
        first = await get_questions(self.db, date(2026, 10, 13))
        second = await get_questions(self.db, date(2026, 10, 13), page=2)
        self.assertEqual((first['total'], second['total']), (25, 25))
        self.assertEqual((len(first['items']), len(second['items'])), (20, 5))
        ids = [item['record_id'] for item in first['items'] + second['items']]
        self.assertEqual(ids, [f'question-{index}' for index in reversed(range(25))])

    async def test_excel_serial_dates_from_real_mirror_data(self):
        parsed = parse_report_time('46261.767245')
        self.assertEqual(parsed.date(), date(2026, 8, 27))
        self.insert('excel-string', {REPORT_TIME_FIELD: '46261.767245'})
        self.insert('excel-number', {REPORT_TIME_FIELD: 46261.767245})
        self.insert('excel-midnight', {REPORT_TIME_FIELD: '46261'})
        self.insert('excel-next-day', {REPORT_TIME_FIELD: '46262.1'})
        result = await get_questions(self.db, date(2026, 8, 27))
        self.assertEqual(result['total'], 3)
        self.assertEqual([item['record_id'] for item in result['items']], ['excel-number', 'excel-string', 'excel-midnight'])

    async def test_invalid_empty_or_missing_dates_do_not_use_sync_time(self):
        for index, values in enumerate([
            {}, {REPORT_TIME_FIELD: None}, {REPORT_TIME_FIELD: ''},
            {REPORT_TIME_FIELD: '2026/10/13 not-a-time'}, [],
        ]):
            self.insert(str(index), values)
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual(result['items'], [])
        self.assertEqual(result['total'], 0)

    async def test_empty_fields_are_normalized_and_lists_preserve_values(self):
        self.insert('empty', {
            REPORT_TIME_FIELD: '2026-10-13', '问题描述': None,
            '问题解决描述': [{'text': '检查'}, {'text': '修复'}], '车辆编号和车型': None,
        })
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual(result['items'][0]['problem_description'], '')
        self.assertEqual(result['items'][0]['solution_description'], '检查、修复')
        self.assertEqual(result['items'][0]['vehicle_info'], '')

    async def test_empty_source(self):
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual(result['total'], 0)
        self.assertIsNone(result['last_sync_at'])

    async def test_blank_full_date_field_falls_back_to_short_field(self):
        self.insert('fallback', {REPORT_TIME_FIELD: '  ', '上报时间': '2026/10/13 2:00 PM'})
        result = await get_questions(self.db, date(2026, 10, 13))
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['reported_at'], '2026-10-13T14:00:00+08:00')


class TestQuestionDateParsing(IsolatedAsyncioTestCase):
    def test_supported_formats(self):
        for value in ['2026/7/29 2:54 PM', '2026/07/29 14:54', '2026-07-29T14:54:00+08:00']:
            with self.subTest(value=value):
                self.assertEqual(parse_report_time(value).isoformat(), '2026-07-29T14:54:00+08:00')
        self.assertEqual(parse_report_time('2026/7/29 12:00 PM').hour, 12)
        self.assertEqual(parse_report_time('2026/7/29 12:00 AM').hour, 0)

    def test_invalid_formats(self):
        for value in [None, '', True, {}, [], '2026/2/30 2:00 PM', 'not a date', '0', '2958466']:
            with self.subTest(value=value):
                self.assertIsNone(parse_report_time(value))


class TestQuestionDashboardApi(IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(dashboard_router, prefix='/api/admin')
        self.user = {'permissions': ['backend:project:all']}
        self.app.dependency_overrides[get_current_active_user_from_token] = lambda: self.user
        self.app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_permission_enforced_on_server(self):
        self.user = {'permissions': ['frontend:admin:other:show'], 'projectIds': ['P1']}
        with patch('app.modules.admin.api.dashboard.get_questions', new_callable=AsyncMock) as fetch:
            response = self.client.get('/api/admin/dashboard/questions?report_date=2026-10-13&project_ids=P1')
        self.assertEqual(response.status_code, 403)
        fetch.assert_not_awaited()

    def test_forwards_validated_date_and_pagination(self):
        with patch('app.modules.admin.api.dashboard.get_questions', new_callable=AsyncMock, return_value={'total': 25}) as fetch:
            response = self.client.get('/api/admin/dashboard/questions?report_date=2026-10-13&page=2&page_size=10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'code': 0, 'data': {'total': 25}})
        fetch.assert_awaited_once_with(None, date(2026, 10, 13), 2, 10)

    def test_rejects_invalid_date_and_pagination(self):
        for query in ['report_date=2026-02-30', 'report_date=2026-10-13&page=0', 'report_date=2026-10-13&page_size=101']:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f'/api/admin/dashboard/questions?{query}').status_code, 422)

    def test_admin_permission_is_supported(self):
        self.user = {'permissions': ['admin']}
        with patch('app.modules.admin.api.dashboard.get_questions', new_callable=AsyncMock, return_value={'total': 0}):
            self.assertEqual(self.client.get('/api/admin/dashboard/questions?report_date=2026-10-13').status_code, 200)
