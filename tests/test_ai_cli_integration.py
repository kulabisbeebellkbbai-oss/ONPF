from pathlib import Path
import pytest

from onpf import cli


def test_offline_gateway_check_does_not_initialize_storage(tmp_path, monkeypatch, capsys):
    key = tmp_path / 'key'
    key.write_text('fictional-key')
    key.chmod(0o600)
    monkeypatch.setenv('ONPF_AI_DRAFTING_ENABLED', 'true')
    monkeypatch.setenv('ONPF_AI_GATEWAY_KEY_FILE', str(key))
    monkeypatch.setenv('ONPF_AI_MODEL', 'onpf-drafting')
    monkeypatch.setattr(cli, 'create_app', lambda *_: (_ for _ in ()).throw(AssertionError('database must stay unopened')))
    instance = tmp_path / 'nonexistent-instance'
    assert cli.main(['--instance', str(instance), 'ai-check']) == 0
    assert not instance.exists()
    assert capsys.readouterr().out == 'ai_configuration_validated_no_request_sent\n'


@pytest.mark.parametrize('name', ['AI_BASE_URL', 'AI_API_KEY', 'AI_API_KEY_FILE'])
@pytest.mark.parametrize('factory', ['settings', 'app', 'production'])
def test_legacy_mapping_fails_before_factory_filters_or_creates_storage(tmp_path, name, factory):
    from onpf.app import create_app
    from onpf.production import create_production_app
    from onpf.drafting.config import validate_settings
    instance = tmp_path / 'uncreated'
    config = {'INSTANCE_PATH': str(instance), name: 'PRIVATE-LEGACY-CONFIGURATION'}
    function = {'settings': validate_settings, 'app': create_app,
                'production': create_production_app}[factory]
    with pytest.raises(ValueError, match='separate gateway') as error:
        function(config)
    assert 'PRIVATE-LEGACY-CONFIGURATION' not in str(error.value)
    assert not instance.exists()


def test_legacy_provider_check_fails_without_revealing_key_or_initializing_storage(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ONPF_AI_API_KEY', 'SECRET-TEST-CREDENTIAL')
    instance = tmp_path / 'nonexistent-instance'
    assert cli.main(['--instance', str(instance), 'ai-check']) == 1
    result = capsys.readouterr()
    assert 'configuration is invalid' in result.err
    assert 'SECRET-TEST-CREDENTIAL' not in result.err
    assert not instance.exists()
