"""Public ad configuration. No market/storage dependency; no secret values returned."""
import json
import os
import re

DISPLAY_PLACEMENTS = ('home_history_below', 'prediction_footer', 'stock_simulation_above', 'monitor_footer')
REWARDED_FEATURES = ('simulation_10d', 'prediction_next')


def default_config():
    return {'configVersion': 'ads-v2-2', 'ads': {
        'enabled': False, 'allowedVersions': ['2.0.0'], 'cacheTtlSeconds': 600,
        'placements': {key: {'enabled': False, 'type': 'ad-custom', 'unitId': ''} for key in DISPLAY_PLACEMENTS},
        'rewarded': {key: {'enabled': False, 'type': 'rewarded-video', 'unitId': ''} for key in REWARDED_FEATURES}},
        'featureGates': {key: {'mode': 'free'} for key in REWARDED_FEATURES}}


def validate_config(value):
    def require(condition):
        if not condition:
            raise ValueError('广告配置无效')

    try:
        require(isinstance(value, dict) and set(value) == {'configVersion', 'ads', 'featureGates'})
        require(isinstance(value['configVersion'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,80}', value['configVersion']))
        ads = value['ads']
        require(isinstance(ads, dict) and set(ads) == {'enabled', 'allowedVersions', 'cacheTtlSeconds', 'placements', 'rewarded'})
        require(type(ads['enabled']) is bool)
        require(type(ads['cacheTtlSeconds']) is int and ads['cacheTtlSeconds'] == 600)
        require(isinstance(ads['allowedVersions'], list) and 1 <= len(ads['allowedVersions']) <= 20)
        require(all(isinstance(v, str) and re.fullmatch(r'\d+\.\d+\.\d+', v) for v in ads['allowedVersions']))
        for group, keys, kind in [('placements', DISPLAY_PLACEMENTS, 'ad-custom'), ('rewarded', REWARDED_FEATURES, 'rewarded-video')]:
            require(isinstance(ads[group], dict) and set(ads[group]) == set(keys))
            for slot in ads[group].values():
                require(isinstance(slot, dict) and set(slot) == {'enabled', 'type', 'unitId'})
                require(type(slot['enabled']) is bool and slot['type'] == kind)
                require(isinstance(slot['unitId'], str))
                require(not slot['unitId'] or re.fullmatch(r'adunit-[A-Za-z0-9]{1,64}', slot['unitId']))
                require(not slot['enabled'] or bool(slot['unitId']))
        gates = value['featureGates']
        require(isinstance(gates, dict) and set(gates) == set(REWARDED_FEATURES))
        for feature, gate in gates.items():
            require(isinstance(gate, dict) and set(gate) == {'mode'} and gate['mode'] in ('free', 'rewarded'))
            require(gate['mode'] != 'rewarded' or (ads['enabled'] and ads['rewarded'][feature]['enabled']))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('广告配置无效') from None
    return value


def load_client_config(environ=None):
    raw = (os.environ if environ is None else environ).get('CLIENT_ADS_CONFIG_JSON', '')
    if not raw:
        return default_config()
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError('广告配置无效') from None
    return validate_config(value)


def next_prediction_status(payload):
    """Metadata only, NOT proof of video completion or API authorization."""
    target = str(payload.get('targetTradeDate') or '')
    available = bool(payload.get('nextDayAvailable') is True
                     and not payload.get('dataUnavailable')
                     and payload.get('datasetStatus') == 'ready'
                     and payload.get('dataStage') in ('initial', 'formal')
                     and payload.get('datasetQuoteComplete') is True
                     and re.fullmatch(r'\d{8}', target))
    return {'available': available, 'targetTradeDate': target if available else '',
            'dataStage': payload.get('dataStage', '') if available else '',
            'reason': '' if available else (payload.get('nextDayReason') or '次日预测数据暂不可用')}
