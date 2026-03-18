// Modal.js
import React, { useState, useEffect } from 'react';
import API from '../../api';
import useUserAgentsStore from '../../store/userAgents';
import M3UProfiles from './M3UProfiles';
import {
  LoadingOverlay,
  TextInput,
  Button,
  Alert,
  Checkbox,
  Modal,
  Flex,
  Select,
  FileInput,
  NumberInput,
  Divider,
  Stack,
  Group,
  Switch,
  Box,
  PasswordInput,
  Collapse,
  Text,
} from '@mantine/core';
import M3UGroupFilter from './M3UGroupFilter';
import useChannelsStore from '../../store/channels';
import { notifications } from '@mantine/notifications';
import { isNotEmpty, useForm } from '@mantine/form';
import useEPGsStore from '../../store/epgs';
import useVODStore from '../../store/useVODStore';
import M3UFilters from './M3UFilters';
import ScheduleInput from './ScheduleInput';
import { DateTimePicker } from '@mantine/dates';

const M3U = ({
  m3uAccount = null,
  isOpen,
  onClose,
  playlistCreated = false,
}) => {
  const userAgents = useUserAgentsStore((s) => s.userAgents);
  const fetchChannelGroups = useChannelsStore((s) => s.fetchChannelGroups);
  const fetchEPGs = useEPGsStore((s) => s.fetchEPGs);
  const fetchCategories = useVODStore((s) => s.fetchCategories);

  const [playlist, setPlaylist] = useState(null);
  const [file, setFile] = useState(null);
  const [expDate, setExpDate] = useState(null);
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [groupFilterModalOpen, setGroupFilterModalOpen] = useState(false);
  const [filterModalOpen, setFilterModalOpen] = useState(false);
  const loadingText = '';
  const [showCredentialFields, setShowCredentialFields] = useState(false);
  const [scheduleType, setScheduleType] = useState('interval');
  const [showAdvancedDeviceFields, setShowAdvancedDeviceFields] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);

  const form = useForm({
    mode: 'uncontrolled',
    initialValues: {
      name: '',
      server_url: '',
      user_agent: '0',
      is_active: true,
      max_streams: 0,
      refresh_interval: 24,
      cron_expression: '',
      account_type: 'XC',
      create_epg: false,
      username: '',
      password: '',
      mac: '',
      model: '',
      serial_number: '',
      device_id: '',
      device_id2: '',
      signature: '',
      timezone: '',
      stale_stream_days: 7,
      priority: 0,
      enable_vod: false,
    },

    validate: {
      name: isNotEmpty('Please select a name'),
      user_agent: isNotEmpty('Please select a user-agent'),
    },
  });

  useEffect(() => {
    if (m3uAccount) {
      setPlaylist(m3uAccount);
      form.setValues({
        name: m3uAccount.name,
        server_url: m3uAccount.server_url,
        max_streams: m3uAccount.max_streams,
        user_agent: m3uAccount.user_agent ? `${m3uAccount.user_agent}` : '0',
        is_active: m3uAccount.is_active,
        refresh_interval: m3uAccount.refresh_interval,
        cron_expression: m3uAccount.cron_expression || '',
        account_type: m3uAccount.account_type,
        username: m3uAccount.username ?? '',
        password: '',
        mac: m3uAccount.mac ?? '',
        model: m3uAccount.model ?? '',
        serial_number: m3uAccount.serial_number ?? '',
        device_id: m3uAccount.device_id ?? '',
        device_id2: m3uAccount.device_id2 ?? '',
        signature: m3uAccount.signature ?? '',
        timezone: m3uAccount.timezone ?? '',
        stale_stream_days:
          m3uAccount.stale_stream_days !== undefined &&
          m3uAccount.stale_stream_days !== null
            ? m3uAccount.stale_stream_days
            : 7,
        priority:
          m3uAccount.priority !== undefined && m3uAccount.priority !== null
            ? m3uAccount.priority
            : 0,
        enable_vod: m3uAccount.enable_vod || false,
      });
      setExpDate(m3uAccount.exp_date ? new Date(m3uAccount.exp_date) : null);

      // Determine schedule type from existing data
      setScheduleType(
        m3uAccount.cron_expression && m3uAccount.cron_expression.trim() !== ''
          ? 'cron'
          : 'interval'
      );

      setShowCredentialFields(
        m3uAccount.account_type === 'XC' ||
          m3uAccount.account_type === 'STALKER'
      );
      setShowAdvancedDeviceFields(
        Boolean(
          m3uAccount.model ||
            m3uAccount.serial_number ||
            m3uAccount.device_id ||
            m3uAccount.device_id2 ||
            m3uAccount.signature ||
            m3uAccount.timezone
        )
      );
    } else {
      setPlaylist(null);
      form.reset();
      setScheduleType('interval');
      setExpDate(null);
      setShowCredentialFields(false);
      setShowAdvancedDeviceFields(false);
    }
  }, [m3uAccount]);

  useEffect(() => {
    setShowCredentialFields(
      form.values.account_type === 'XC' ||
        form.values.account_type === 'STALKER'
    );
    if (form.values.account_type !== 'STD') {
      setFile(null);
    }
    if (form.values.account_type !== 'STALKER') {
      setShowAdvancedDeviceFields(false);
    }
  }, [form.values.account_type]);

  const onSubmit = async () => {
    const { create_epg, ...values } = form.getValues();

    // Convert exp_date (from controlled state) to ISO string for the API
    if (values.account_type === 'XC') {
      // XC accounts have exp_date auto-managed server-side; don't send it
      delete values.exp_date;
    } else if (values.account_type === 'STALKER') {
      values.exp_date = null;
    } else if (expDate instanceof Date) {
      values.exp_date = expDate.toISOString();
    } else {
      values.exp_date = null;
    }

    // Determine which schedule type is active based on field values
    const hasCronExpression =
      values.cron_expression && values.cron_expression.trim() !== '';

    // Clear the field that isn't active based on actual field values
    if (hasCronExpression) {
      values.refresh_interval = 0;
    } else {
      values.cron_expression = '';
    }

    if (values.account_type == 'XC' && values.password == '') {
      // If account XC and no password input, assuming no password change
      // from previously stored value.
      delete values.password;
    }

    if (values.account_type !== 'XC') {
      values.enable_vod = false;
    }

    if (values.account_type !== 'STALKER') {
      delete values.mac;
      delete values.model;
      delete values.serial_number;
      delete values.device_id;
      delete values.device_id2;
      delete values.signature;
      delete values.timezone;
    }

    if (values.user_agent == '0') {
      values.user_agent = null;
    }

    let newPlaylist;
    if (playlist?.id) {
      newPlaylist = await API.updatePlaylist({
        id: playlist.id,
        ...values,
        file,
      });
      if (newPlaylist) {
        setPlaylist(newPlaylist);
      }
    } else {
      newPlaylist = await API.addPlaylist({
        ...values,
        file,
      });

      if (create_epg && values.account_type === 'XC') {
        API.addEPG({
          name: values.name,
          source_type: 'xmltv',
          url: `${new URL(values.server_url).origin}/xmltv.php?username=${values.username}&password=${values.password}`,
          api_key: '',
          is_active: true,
          refresh_interval: 24,
        });
      }

      if (values.account_type === 'STD') {
        notifications.show({
          title: 'Fetching M3U Groups',
          message:
            'Configure group filters and auto sync settings once complete.',
        });

        // Don't prompt for group filters, but keeping this here
        // in case we want to revive it
        newPlaylist = null;
        close();
        return;
      }

      if (values.account_type === 'STALKER') {
        if (newPlaylist) {
          setPlaylist(newPlaylist);
        }
        return;
      }

      // Fetch the updated playlist details (this also updates the store via API)
      const updatedPlaylist = await API.getPlaylist(newPlaylist.id);

      // Note: We don't call fetchPlaylists() here because API.addPlaylist()
      // already added the playlist to the store. Calling fetchPlaylists() creates
      // a race condition where the store is temporarily cleared/replaced while
      // websocket updates for the new playlist's refresh task are arriving.
      await Promise.all([fetchChannelGroups(), fetchEPGs()]);

      // If this is an XC account with VOD enabled, also fetch VOD categories
      if (values.account_type === 'XC' && values.enable_vod) {
        fetchCategories();
      }

      console.log('opening group options');
      setPlaylist(updatedPlaylist);
      setGroupFilterModalOpen(true);
      return;
    }

    form.reset();
    setFile(null);
    onClose(newPlaylist);
  };

  const handleTestConnection = async () => {
    if (!playlist?.id) {
      notifications.show({
        title: 'Save Required',
        message: 'Save the Stalker account before testing the connection.',
        color: 'yellow',
      });
      return;
    }

    setTestingConnection(true);
    try {
      const response = await API.testStalkerConnection(playlist.id);
      if (response?.account) {
        setPlaylist(response.account);
      }
      notifications.show({
        title: 'Connection Successful',
        message: response?.message || 'The Stalker portal connection succeeded.',
        color: 'green',
      });
    } finally {
      setTestingConnection(false);
    }
  };

  const close = () => {
    form.reset();
    setFile(null);
    setPlaylist(null);
    onClose();
  };

  const closeGroupFilter = () => {
    setGroupFilterModalOpen(false);
    // After group filter setup for a new account, reset everything
    form.reset();
    setFile(null);
    setPlaylist(null);
    onClose();
  };

  const closeFilter = () => {
    setFilterModalOpen(false);
  };

  useEffect(() => {
    if (playlistCreated) {
      setGroupFilterModalOpen(true);
    }
  }, [playlist, playlistCreated]);

  if (!isOpen) {
    return <></>;
  }

  const accountType = form.getValues().account_type;
  const isXC = accountType === 'XC';
  const isStalker = accountType === 'STALKER';
  const isStandard = accountType === 'STD';

  return (
    <>
      <Modal
        size={700}
        opened={isOpen}
        onClose={close}
        title="M3U Account"
        scrollAreaComponent={Modal.NativeScrollArea}
        lockScroll={false}
        withinPortal={true}
        trapFocus={false}
        yOffset="2vh"
      >
        <LoadingOverlay
          visible={form.submitting}
          overlayBlur={2}
          loaderProps={loadingText ? { children: loadingText } : {}}
        />

        <form onSubmit={form.onSubmit(onSubmit)}>
          <Group justify="space-between" align="top">
            <Stack gap="5" style={{ flex: 1 }}>
              <TextInput
                style={{ width: '100%' }}
                id="name"
                name="name"
                label="Name"
                description="Unique identifier for this M3U account"
                {...form.getInputProps('name')}
                key={form.key('name')}
              />
              <TextInput
                style={{ width: '100%' }}
                id="server_url"
                name="server_url"
                label={isStalker ? 'Portal URL' : 'URL'}
                description={
                  isStalker
                    ? 'Base Stalker portal URL'
                    : 'Direct URL to the M3U playlist or server'
                }
                {...form.getInputProps('server_url')}
                key={form.key('server_url')}
              />

              <Select
                id="account_type"
                name="account_type"
                label="Account Type"
                description={
                  <>
                    Standard for direct M3U URLs, <br />
                    Xtream Codes for panel-based services, <br />
                    Stalker for portal-based live TV services
                  </>
                }
                data={[
                  {
                    value: 'STD',
                    label: 'Standard',
                  },
                  {
                    value: 'XC',
                    label: 'Xtream Codes',
                  },
                  {
                    value: 'STALKER',
                    label: 'Stalker',
                  },
                ]}
                key={form.key('account_type')}
                {...form.getInputProps('account_type')}
              />

              {isXC && (
                <Box>
                  {!m3uAccount && (
                    <Group justify="space-between">
                      <Box>Create EPG</Box>
                      <Switch
                        id="create_epg"
                        name="create_epg"
                        description="Automatically create matching EPG source for this Xtream account"
                        key={form.key('create_epg')}
                        {...form.getInputProps('create_epg', {
                          type: 'checkbox',
                        })}
                      />
                    </Group>
                  )}

                  <Group justify="space-between">
                    <Box>Enable VOD Scanning</Box>
                    <Switch
                      id="enable_vod"
                      name="enable_vod"
                      description="Scan and import VOD content (movies/series) from this Xtream account"
                      key={form.key('enable_vod')}
                      {...form.getInputProps('enable_vod', {
                        type: 'checkbox',
                      })}
                    />
                  </Group>

                  <TextInput
                    id="username"
                    name="username"
                    label="Username"
                    description="Username for Xtream Codes authentication"
                    {...form.getInputProps('username')}
                  />

                  <PasswordInput
                    id="password"
                    name="password"
                    label="Password"
                    description="Password for Xtream Codes authentication (leave empty to keep existing)"
                    {...form.getInputProps('password')}
                  />
                </Box>
              )}

              {isStalker && (
                <Stack gap="sm">
                  <TextInput
                    id="mac"
                    name="mac"
                    label="MAC Address"
                    description="Portal device MAC address"
                    placeholder="00:1A:79:00:00:00"
                    {...form.getInputProps('mac')}
                  />

                  {showCredentialFields && (
                    <>
                      <TextInput
                        id="username"
                        name="username"
                        label="Username"
                        description="Optional portal username"
                        {...form.getInputProps('username')}
                      />

                      <PasswordInput
                        id="password"
                        name="password"
                        label="Password"
                        description="Optional portal password"
                        {...form.getInputProps('password')}
                      />
                    </>
                  )}

                  <Button
                    type="button"
                    variant="subtle"
                    size="xs"
                    style={{ alignSelf: 'flex-start' }}
                    onClick={() =>
                      setShowAdvancedDeviceFields((current) => !current)
                    }
                  >
                    {showAdvancedDeviceFields
                      ? 'Hide Advanced Device Fields'
                      : 'Show Advanced Device Fields'}
                  </Button>

                  <Collapse in={showAdvancedDeviceFields}>
                    <Stack gap="sm">
                      <TextInput
                        id="model"
                        name="model"
                        label="Model"
                        description="Optional device model"
                        {...form.getInputProps('model')}
                      />
                      <TextInput
                        id="serial_number"
                        name="serial_number"
                        label="Serial Number"
                        description="Optional device serial number"
                        {...form.getInputProps('serial_number')}
                      />
                      <TextInput
                        id="device_id"
                        name="device_id"
                        label="Device ID"
                        description="Optional device identifier"
                        {...form.getInputProps('device_id')}
                      />
                      <TextInput
                        id="device_id2"
                        name="device_id2"
                        label="Device ID 2"
                        description="Optional secondary device identifier"
                        {...form.getInputProps('device_id2')}
                      />
                      <TextInput
                        id="signature"
                        name="signature"
                        label="Signature"
                        description="Optional device signature"
                        {...form.getInputProps('signature')}
                      />
                      <TextInput
                        id="timezone"
                        name="timezone"
                        label="Timezone"
                        description="Optional portal timezone"
                        {...form.getInputProps('timezone')}
                      />
                    </Stack>
                  </Collapse>

                  {playlist?.last_message && (
                    <Alert
                      color={playlist.status === 'error' ? 'red' : 'green'}
                      variant="light"
                    >
                      <Text size="sm">{playlist.last_message}</Text>
                    </Alert>
                  )}
                </Stack>
              )}

              {isStandard && (
                <>
                  <FileInput
                    id="file"
                    label="Upload files"
                    placeholder="Upload files"
                    description="Upload a local M3U file instead of using URL"
                    onChange={setFile}
                  />

                  <DateTimePicker
                    label="Expiration Date"
                    description="Set an expiration date to receive a warning notification"
                    placeholder="No expiration"
                    clearable
                    valueFormat="MMM D, YYYY h:mm A"
                    value={expDate}
                    onChange={(v) => setExpDate(v ? new Date(v) : null)}
                  />
                </>
              )}
            </Stack>

            <Divider size="sm" orientation="vertical" />

            <Stack gap="5" style={{ flex: 1 }}>
              <TextInput
                style={{ width: '100%' }}
                id="max_streams"
                name="max_streams"
                label="Max Streams"
                placeholder="0 = Unlimited"
                description="Maximum number of concurrent streams (0 for unlimited)"
                {...form.getInputProps('max_streams')}
                key={form.key('max_streams')}
              />

              <Select
                id="user_agent"
                name="user_agent"
                label="User-Agent"
                description="User-Agent header to use when accessing this M3U source"
                {...form.getInputProps('user_agent')}
                key={form.key('user_agent')}
                data={[{ value: '0', label: '(Use Default)' }].concat(
                  userAgents.map((ua) => ({
                    label: ua.name,
                    value: `${ua.id}`,
                  }))
                )}
              />

              <ScheduleInput
                scheduleType={scheduleType}
                onScheduleTypeChange={setScheduleType}
                intervalValue={form.getValues().refresh_interval}
                onIntervalChange={(v) =>
                  form.setFieldValue('refresh_interval', v)
                }
                cronValue={form.getValues().cron_expression}
                onCronChange={(expr) =>
                  form.setFieldValue('cron_expression', expr)
                }
                intervalLabel="Refresh Interval (hours)"
                intervalDescription={
                  <>
                    How often to automatically refresh M3U data
                    <br />
                    (0 to disable automatic refreshes)
                  </>
                }
              />

              <NumberInput
                min={0}
                max={365}
                label="Stale Stream Retention (days)"
                description="Streams not seen for this many days will be removed"
                {...form.getInputProps('stale_stream_days')}
              />

              <NumberInput
                min={0}
                max={999}
                label="VOD Priority"
                description="Priority for VOD provider selection (higher numbers = higher priority). Used when multiple providers offer the same content."
                {...form.getInputProps('priority')}
                key={form.key('priority')}
                disabled={!isXC}
              />

              <Checkbox
                label="Is Active"
                description="Enable or disable this M3U account"
                {...form.getInputProps('is_active', { type: 'checkbox' })}
                key={form.key('is_active')}
              />
            </Stack>
          </Group>

          <Flex mih={50} gap="xs" justify="flex-end" align="flex-end">
            {playlist && (
              <>
                {!isStalker && (
                  <Button
                    variant="filled"
                    size="sm"
                    onClick={() => setFilterModalOpen(true)}
                  >
                    Filters
                  </Button>
                )}
                <Button
                  variant="filled"
                  size="sm"
                  onClick={() => {
                    if (
                      m3uAccount?.account_type === 'XC' &&
                      m3uAccount?.enable_vod
                    ) {
                      fetchCategories();
                    }
                    setGroupFilterModalOpen(true);
                  }}
                >
                  Groups
                </Button>
                <Button
                  variant="filled"
                  size="sm"
                  onClick={() => setProfileModalOpen(true)}
                >
                  Profiles
                </Button>
                {isStalker && (
                  <Button
                    type="button"
                    variant="light"
                    size="sm"
                    onClick={handleTestConnection}
                    loading={testingConnection}
                  >
                    Test Connection
                  </Button>
                )}
              </>
            )}

            <Button
              type="submit"
              variant="filled"
              disabled={form.submitting}
              size="sm"
            >
              Save
            </Button>
          </Flex>
        </form>
      </Modal>
      {playlist && (
        <>
          <M3UProfiles
            playlist={playlist}
            isOpen={profileModalOpen}
            onClose={() => setProfileModalOpen(false)}
          />
          <M3UGroupFilter
            isOpen={groupFilterModalOpen}
            playlist={playlist}
            onClose={closeGroupFilter}
          />
          <M3UFilters
            isOpen={filterModalOpen}
            playlist={playlist}
            onClose={closeFilter}
          />
        </>
      )}
    </>
  );
};

export default M3U;
