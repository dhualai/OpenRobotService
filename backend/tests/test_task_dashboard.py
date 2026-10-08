from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from sqlalchemy import MetaData
from sqlalchemy.engine.create import create_engine

from app.models.task import Task, TaskStatus
from app.modules.admin.api.dashboard import get_tickets_by_status
from app.modules.admin.services.task_dashboard_service import TaskDashboardService


class _SqliteDb:
    def __init__(self, connection):
        self.connection = connection

    async def execute(self, query):
        result = self.connection.execute(query)
        if len(query.column_descriptions) == 1 and query.column_descriptions[0]['expr'] is Task:
            items = [SimpleNamespace(**row) for row in result.mappings().all()]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))
        return result


class TestTaskDashboard(IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        metadata = MetaData()
        self.table = Task.__table__.to_metadata(metadata)
        metadata.create_all(self.engine)
        self.connection = self.engine.connect()
        self.db = _SqliteDb(self.connection)
        self.now = datetime.now()
        rows = []
        for project_id in ['P1', 'P2']:
            for status in TaskStatus:
                rows.append(self.make_task(len(rows) + 1, project_id, status))
        for _ in range(25):
            rows.append(self.make_task(len(rows) + 1, 'P1', TaskStatus.IN_PROGRESS))
        rows.append(self.make_task(len(rows) + 1, 'P1', TaskStatus.IN_PROGRESS, deadline_at=None))
        rows.append(self.make_task(
            len(rows) + 1, 'P1', TaskStatus.IN_PROGRESS, deadline_at=self.now + timedelta(days=1),
        ))
        self.connection.execute(self.table.insert(), rows)
        self.user_patch = patch(
            'app.modules.admin.services.task_dashboard_service.user_service.get_user_map', return_value={},
        )
        self.user_patch.start()

    def tearDown(self):
        self.user_patch.stop()
        self.connection.close()
        self.engine.dispose()

    def make_task(self, task_id, project_id, status, **overrides):
        return {
            'id': task_id,
            'title': f'Ticket {task_id}',
            'description': '',
            'created_by': 'user1',
            'status': status,
            'project_id': project_id,
            'created_at': self.now,
            'updated_at': self.now,
            'deadline_at': self.now - timedelta(days=1),
            **overrides,
        }

    async def test_summary_matches_all_three_lists_for_each_project_scope(self):
        for project_ids in [None, ['P1'], ['P2'], [], ['missing']]:
            summary = await TaskDashboardService.get_ticket_summary(self.db, project_ids)
            for scope, summary_key in [('all', 'total'), ('pending', 'pending_count'), ('overdue', 'overdue_count')]:
                with self.subTest(project_ids=project_ids, scope=scope):
                    result = await TaskDashboardService.get_tickets_by_status(
                        self.db, scope, limit=100, project_ids=project_ids,
                    )
                    self.assertEqual(result['total'], summary[summary_key])
                    self.assertEqual(len(result['items']), summary[summary_key])

    async def test_pending_excludes_new_and_pending_requested(self):
        summary = await TaskDashboardService.get_ticket_summary(self.db, ['P2'])
        result = await TaskDashboardService.get_tickets_by_status(self.db, 'pending', project_ids=['P2'])
        self.assertEqual(summary['pending_count'], 2)
        self.assertEqual({item['status'] for item in result['items']}, {'in_progress', 'pending'})

    async def test_overdue_includes_pending_requested_but_not_finished_or_new(self):
        result = await TaskDashboardService.get_tickets_by_status(self.db, 'overdue', limit=100)
        self.assertEqual(result['total'], 31)
        self.assertEqual(
            {item['status'] for item in result['items']}, {'in_progress', 'pending_requested', 'pending'},
        )
        self.assertEqual([item['status'] for item in result['items'][:2]], ['pending', 'pending'])

    async def test_pagination_retrieves_every_ticket_without_duplicates(self):
        for scope in ['all', 'pending', 'overdue']:
            with self.subTest(scope=scope):
                expected = await TaskDashboardService.get_tickets_by_status(self.db, scope, limit=100)
                items = []
                for skip in range(0, expected['total'], 20):
                    page = await TaskDashboardService.get_tickets_by_status(self.db, scope, skip=skip, limit=20)
                    self.assertEqual(page['total'], expected['total'])
                    self.assertLessEqual(len(page['items']), 20)
                    items.extend(page['items'])
                self.assertEqual(items, expected['items'])
                self.assertEqual(len({item['id'] for item in items}), expected['total'])

    async def test_api_forwards_pagination_and_project_scope(self):
        with patch(
            'app.modules.admin.api.dashboard.task_dashboard_service.get_tickets_by_status',
            new_callable=AsyncMock,
            return_value={'items': [], 'total': 25},
        ) as fetch:
            result = await get_tickets_by_status(status='all', project_ids='P1', skip=20, limit=20, db=self.db)
            fetch.assert_awaited_once_with(self.db, 'all', skip=20, limit=20, project_ids=['P1'])
            self.assertEqual(result['data']['total'], 25)
