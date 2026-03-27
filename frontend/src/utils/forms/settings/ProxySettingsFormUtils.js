import { PROXY_SETTINGS_OPTIONS } from '../../../constants.js';

export const getProxySettingsFormInitialValues = () => {
  return Object.keys(PROXY_SETTINGS_OPTIONS).reduce((acc, key) => {
    acc[key] = '';
    return acc;
  }, {});
};

export const getProxySettingDefaults = () => {
  return {
    buffering_timeout: 15,
    buffering_speed: 1.0,
    redis_chunk_ttl: 60,
    channel_shutdown_delay: 0,
    channel_init_grace_period: 5,
    new_client_behind_seconds: 15,
    connection_ready_chunks: 16,
    max_reconnect_attempts: 5,
    min_stable_time_before_reconnect: 10,
  };
};
