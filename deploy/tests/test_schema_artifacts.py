from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import deploy as deployment


@pytest.fixture
def source_tree(tmp_path):
    source = tmp_path / 'source'
    (source / 'backend' / 'app').mkdir(parents=True)
    (source / 'backend' / 'app' / '__init__.py').write_text('', encoding='utf-8')
    (source / 'backend' / 'main.py').write_text('', encoding='utf-8')
    (source / 'backend' / 'scripts').mkdir()
    (source / 'backend' / 'scripts' / 'schema_sync.py').write_text('print("schema helper")\n', encoding='utf-8')
    return source


def build_backend_artifacts(source, tmp_path):
    artifact_dir = tmp_path / 'artifacts'
    deployment.build_artifacts(source, ['backend'], 'test', 'build:test', artifact_dir)
    return artifact_dir


def test_backend_build_bundles_schema_helper(source_tree, tmp_path):
    artifact_dir = build_backend_artifacts(source_tree, tmp_path)
    helper = artifact_dir / 'deploy' / 'schema_sync.py'
    assert helper.read_bytes() == (source_tree / 'backend' / 'scripts' / 'schema_sync.py').read_bytes()
    assert (artifact_dir / 'BUILD_META.json').is_file()


def test_backend_build_rejects_missing_helper_before_packaging(tmp_path, monkeypatch):
    package = Mock()
    monkeypatch.setattr(deployment, 'component_artifact', package)
    with pytest.raises(RuntimeError, match='缺少结构校验脚本'):
        deployment.build_artifacts(tmp_path, ['backend'], 'test', 'build:test', tmp_path / 'artifacts')
    package.assert_not_called()


def test_frontend_build_does_not_require_backend_schema_helper(tmp_path):
    (tmp_path / 'frontend' / 'dist').mkdir(parents=True)
    (tmp_path / 'frontend' / 'dist' / 'index.html').write_text('ready', encoding='utf-8')
    artifact_dir = tmp_path / 'artifacts'
    deployment.build_artifacts(tmp_path, ['frontend'], 'test', 'build:test', artifact_dir, skip_build=True)
    assert (artifact_dir / 'BUILD_META.json').is_file()
    assert not (artifact_dir / 'deploy' / 'schema_sync.py').exists()


@pytest.mark.parametrize('mode', ['check', 'sync'])
def test_staged_backend_can_run_schema_gate_without_adjacent_helper(source_tree, tmp_path, monkeypatch, mode):
    artifact_dir = build_backend_artifacts(source_tree, tmp_path)
    stage = tmp_path / 'stage'
    stage.mkdir()
    monkeypatch.setattr(deployment, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(stage)))
    monkeypatch.setattr(deployment, '__file__', str(tmp_path / 'runner' / 'deploy.py'))
    staged = deployment.stage_artifacts(artifact_dir, 'test')
    helper = staged / 'backend' / 'scripts' / 'schema_sync.py'
    assert helper.read_bytes() == (artifact_dir / 'deploy' / 'schema_sync.py').read_bytes()
    send_file = Mock()
    remote = Mock(return_value='SCHEMA_EXIT=0\nSCHEMA_DATABASE=test_db\n')
    monkeypatch.setattr(deployment, 'send_file', send_file)
    monkeypatch.setattr(deployment, 'run_remote_script', remote)
    config = deployment.SshConfig('', '', 22, '', '', local=True)
    summary = deployment.deploy_schema_sync(config, staged, deployment.get_env_config('test'), mode)
    send_file.assert_called_once_with(config, str(helper), 'tmp/ors_schema_sync.py')
    command = remote.call_args.args[1]
    assert f'--backend-root "{staged / "backend"}"' in command
    assert (' --execute' in command) == (mode == 'sync')
    assert 'test_db' in summary


def test_legacy_adjacent_schema_helper_remains_supported(tmp_path, monkeypatch):
    script_dir = tmp_path / 'deploy'
    script_dir.mkdir()
    helper = script_dir / 'schema_sync.py'
    helper.write_text('legacy helper', encoding='utf-8')
    monkeypatch.setattr(deployment, '__file__', str(script_dir / 'deploy.py'))
    assert deployment._schema_sync_script(tmp_path / 'staged') == helper


@pytest.mark.parametrize('mode', ['check', 'sync'])
def test_missing_helper_stops_before_remote_backup(tmp_path, monkeypatch, capsys, mode):
    backup = Mock()
    backend = Mock()
    config = deployment.SshConfig('', '', 22, '', '', local=True)
    monkeypatch.setattr(deployment, 'build_ssh_config', lambda args: config)
    monkeypatch.setattr(deployment, 'test_command', lambda command: True)
    monkeypatch.setattr(deployment, 'create_backup', backup)
    monkeypatch.setattr(deployment, 'deploy_backend', backend)
    monkeypatch.setattr(deployment, '__file__', str(tmp_path / 'runner' / 'deploy.py'))
    args = deployment.build_parser().parse_args([
        '--project-path', str(tmp_path), '-c', 'backend', '--local', '--yes', '--schema-mode', mode,
    ])
    assert deployment.main_cli(args) == 1
    backup.assert_not_called()
    backend.assert_not_called()
    assert '重新构建产物' in capsys.readouterr().out
