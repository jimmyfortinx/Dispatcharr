import useSettingsStore from '../../../store/settings.jsx';
import React, { useEffect, useState } from 'react';
import {
  getChangedSettings,
  parseSettings,
  saveChangedSettings,
} from '../../../utils/pages/SettingsUtils.js';
import {
  Alert,
  Button,
  Divider,
  Flex,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
} from '@mantine/core';
import ConnectionSecurityPanel from './ConnectionSecurityPanel.jsx';
import { useForm } from '@mantine/form';
import { getSystemSettingsFormInitialValues } from '../../../utils/forms/settings/SystemSettingsFormUtils.js';
import { REGION_CHOICES } from '../../../constants.js';

const SystemSettingsForm = React.memo(({ active }) => {
  const settings = useSettingsStore((s) => s.settings);
  const isModular =
    useSettingsStore((s) => s.environment.env_mode) === 'modular';
  const ipLookupEnvDisabled = useSettingsStore(
    (s) => s.environment.ip_lookup_env_disabled
  );

  const [saved, setSaved] = useState(false);
  const [probeModeSaving, setProbeModeSaving] = useState(false);

  const form = useForm({
    mode: 'controlled',
    initialValues: getSystemSettingsFormInitialValues(),
  });

  useEffect(() => {
    if (!active) setSaved(false);
  }, [active]);

  useEffect(() => {
    if (settings) {
      const formValues = parseSettings(settings);

      form.setValues(formValues);
    }
  }, [settings]);

  const onSubmit = async () => {
    setSaved(false);

    const changedSettings = getChangedSettings(form.getValues(), settings);

    // Update each changed setting in the backend (create if missing)
    try {
      await saveChangedSettings(settings, changedSettings);

      setSaved(true);
    } catch (error) {
      // Error notifications are already shown by API functions
      // Just don't show the success message
      console.error('Error saving settings:', error);
    }
  };

  const handleForceProbeModeToggle = async () => {
    if (!settings) return;

    const nextValue = !form.values.force_vod_probe_mode;
    setProbeModeSaving(true);
    setSaved(false);

    try {
      await saveChangedSettings(settings, {
        force_vod_probe_mode: nextValue,
      });
      form.setFieldValue('force_vod_probe_mode', nextValue);
    } catch (error) {
      console.error('Error saving force probe mode setting:', error);
    } finally {
      setProbeModeSaving(false);
    }
  };

  return (
    <Stack gap="md">
      {saved && (
        <Alert variant="light" color="green" title="Saved Successfully" />
      )}
      <NumberInput
        label="Maximum System Events"
        description="Number of events to retain (minimum: 10, maximum: 1000). Events are displayed on the Stats page."
        value={form.values['max_system_events'] || 100}
        onChange={(value) => {
          form.setFieldValue('max_system_events', value);
        }}
        min={10}
        max={1000}
        step={10}
      />
      <Select
        searchable
        clearable
        {...form.getInputProps('preferred_region')}
        id="preferred_region"
        name="preferred_region"
        label="Preferred Region"
        description="Used when matching EPG data to channels. Prioritizes guide entries from the selected region."
        data={REGION_CHOICES.map((r) => ({
          label: r.label,
          value: `${r.value}`,
        }))}
      />
      <Group justify="space-between" pt={5}>
        <div>
          <Text size="sm" fw={500}>
            Auto-Import Mapped Files
          </Text>
          <Text size="xs" c="dimmed">
            Automatically import media files when they are mapped to a channel.
          </Text>
        </div>
        <Switch
          {...form.getInputProps('auto_import_mapped_files', {
            type: 'checkbox',
          })}
          id="auto_import_mapped_files"
        />
      </Group>
      {!ipLookupEnvDisabled && (
        <Group justify="space-between" pt={5}>
          <div>
            <Text size="sm" fw={500}>
              Enable IP Lookup
            </Text>
            <Text size="xs" c="dimmed">
              Fetch and display the instance's public IP and country flag in the
              sidebar.
            </Text>
          </div>
          <Switch
            {...form.getInputProps('enable_ip_lookup', { type: 'checkbox' })}
            id="enable_ip_lookup"
          />
        </Group>
      )}
      {isModular && (
        <>
          <Divider my="md" label="Connection Security" labelPosition="left" />
          <ConnectionSecurityPanel />
        </>
      )}
      <Divider my="md" label="Plex Probe Mode" labelPosition="left" />
      <Group justify="space-between" align="flex-start">
        <div>
          <Text size="sm" fw={500}>
            Manual Probe Override
          </Text>
          <Text size="xs" c="dimmed">
            Force Plex-like Stalker VOD requests into probe mode. Use this only
            while Plex is scanning, because real playback attempts from Plex
            will also get synthetic probe responses until you disable it.
          </Text>
          <Text size="xs" c={form.values.force_vod_probe_mode ? 'orange' : 'dimmed'}>
            {form.values.force_vod_probe_mode
              ? 'Forced probe mode is enabled.'
              : 'Forced probe mode is disabled.'}
          </Text>
        </div>
        <Button
          onClick={handleForceProbeModeToggle}
          disabled={probeModeSaving || !settings}
          variant={form.values.force_vod_probe_mode ? 'filled' : 'default'}
        >
          {form.values.force_vod_probe_mode
            ? 'Disable Forced Probe Mode'
            : 'Force Probe Mode'}
        </Button>
      </Group>
      <Flex mih={50} gap="xs" justify="flex-end" align="flex-end">
        <Button
          onClick={form.onSubmit(onSubmit)}
          disabled={form.submitting}
          variant="default"
        >
          Save
        </Button>
      </Flex>
    </Stack>
  );
});

export default SystemSettingsForm;
