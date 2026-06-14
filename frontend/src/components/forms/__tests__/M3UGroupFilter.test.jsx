import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import M3UGroupFilter from '../M3UGroupFilter';
import API from '../../../api';
import { notifications } from '@mantine/notifications';

vi.mock('../../../store/channels', () => ({ default: vi.fn() }));
vi.mock('../../../store/useVODStore', () => ({ default: vi.fn() }));

vi.mock('../../../api', () => ({
  default: {
    updatePlaylist: vi.fn(),
    updateM3UGroupSettings: vi.fn(),
    refreshPlaylist: vi.fn(),
  },
}));

vi.mock('@mantine/notifications', () => ({
  notifications: {
    show: vi.fn(),
  },
}));

vi.mock('../LiveGroupFilter', () => ({
  default: ({
    groupStates,
    setGroupStates,
    autoEnableNewGroupsLive,
    setAutoEnableNewGroupsLive,
  }) => (
    <div data-testid="live-group-filter">
      <span data-testid="live-group-count">{groupStates?.length ?? 0}</span>
      <button
        type="button"
        data-testid="live-toggle-auto"
        onClick={() => setAutoEnableNewGroupsLive?.(!autoEnableNewGroupsLive)}
      >
        Toggle Auto Live
      </button>
      <button
        type="button"
        data-testid="live-change-groups"
        onClick={() =>
          setGroupStates?.([
            {
              id: 99,
              channel_group: 1,
              name: 'Changed Group',
              enabled: true,
              custom_properties: {},
            },
          ])
        }
      >
        Change Groups
      </button>
    </div>
  ),
}));

vi.mock('../VODCategoryFilter', () => ({
  default: ({
    categoryStates,
    setCategoryStates,
    autoEnableNewGroups,
    setAutoEnableNewGroups,
    type,
  }) => (
    <div data-testid={`vod-category-filter-${type}`}>
      <span data-testid={`${type}-category-count`}>
        {categoryStates?.length ?? 0}
      </span>
      <button
        type="button"
        data-testid={`vod-toggle-auto-${type}`}
        onClick={() => setAutoEnableNewGroups?.(!autoEnableNewGroups)}
      >
        Toggle Auto {type}
      </button>
      <button
        type="button"
        data-testid={`vod-change-${type}`}
        onClick={() =>
          setCategoryStates?.([
            {
              id: 55,
              enabled: type === 'movie',
              original_enabled: false,
              custom_properties: {},
            },
          ])
        }
      >
        Change {type}
      </button>
    </div>
  ),
}));

vi.mock('@mantine/core', () => {
  const Tabs = ({ children, defaultValue }) => (
    <div data-testid="tabs" data-value={defaultValue}>
      {children}
    </div>
  );
  Tabs.List = ({ children }) => <div data-testid="tabs-list">{children}</div>;
  Tabs.Tab = ({ children, value }) => (
    <button type="button" data-testid={`tab-${value}`}>
      {children}
    </button>
  );
  Tabs.Panel = ({ children, value }) => (
    <div data-testid={`tab-panel-${value}`}>{children}</div>
  );

  return {
    Button: ({ children, onClick, disabled, loading, type = 'button' }) => (
      <button
        type={type}
        onClick={onClick}
        disabled={disabled || loading}
        data-loading={loading}
      >
        {children}
      </button>
    ),
    Flex: ({ children }) => <div>{children}</div>,
    LoadingOverlay: ({ visible }) =>
      visible ? <div data-testid="loading-overlay" /> : null,
    Modal: Object.assign(
      ({ children, opened, onClose, title }) =>
        opened ? (
          <div data-testid="modal">
            <div data-testid="modal-title">{title}</div>
            <button data-testid="modal-close" onClick={onClose}>
              ×
            </button>
            {children}
          </div>
        ) : null,
      { NativeScrollArea: 'div' }
    ),
    Stack: ({ children }) => <div>{children}</div>,
    Tabs,
  };
});

import useChannelsStore from '../../../store/channels';
import useVODStore from '../../../store/useVODStore';

const makePlaylist = (overrides = {}) => ({
  id: 1,
  name: 'Test Playlist',
  account_type: 'XC',
  enable_vod: true,
  auto_enable_new_groups_live: true,
  auto_enable_new_groups_vod: true,
  auto_enable_new_groups_series: true,
  channel_groups: [
    {
      id: 10,
      channel_group: 1,
      auto_channel_sync: false,
      auto_sync_channel_start: 1,
      custom_properties: {},
    },
  ],
  ...overrides,
});

const defaultProps = (overrides = {}) => ({
  playlist: makePlaylist(),
  isOpen: true,
  onClose: vi.fn(),
  ...overrides,
});

const setupStores = ({
  channelGroups = {
    1: { id: 1, name: 'Group A' },
    2: { id: 2, name: 'Group B' },
  },
  fetchCategories = vi.fn().mockResolvedValue(undefined),
} = {}) => {
  vi.mocked(useChannelsStore).mockImplementation((sel) => sel({ channelGroups }));
  vi.mocked(useVODStore).mockImplementation((sel) => sel({ fetchCategories }));
  return { channelGroups, fetchCategories };
};

describe('M3UGroupFilter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(API.updatePlaylist).mockResolvedValue(undefined);
    vi.mocked(API.updateM3UGroupSettings).mockResolvedValue(undefined);
    vi.mocked(API.refreshPlaylist).mockResolvedValue(undefined);
  });

  it('does not render when closed', () => {
    setupStores();
    render(<M3UGroupFilter {...defaultProps({ isOpen: false })} />);
    expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
  });

  it('renders live and VOD tabs for supported playlists', async () => {
    const { fetchCategories } = setupStores();
    render(<M3UGroupFilter {...defaultProps()} />);

    expect(screen.getByTestId('modal-title')).toHaveTextContent(
      'M3U Group Filter & Auto Channel Sync'
    );
    expect(screen.getByTestId('tab-live')).toBeInTheDocument();
    expect(screen.getByTestId('tab-vod-movie')).toBeInTheDocument();
    expect(screen.getByTestId('tab-vod-series')).toBeInTheDocument();
    expect(screen.getByTestId('live-group-filter')).toBeInTheDocument();
    expect(screen.getByTestId('vod-category-filter-movie')).toBeInTheDocument();
    expect(screen.getByTestId('vod-category-filter-series')).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchCategories).toHaveBeenCalled();
    });
  });

  it('hides VOD tabs for unsupported playlists', () => {
    setupStores();
    render(
      <M3UGroupFilter
        {...defaultProps({
          playlist: makePlaylist({ account_type: 'M3U', enable_vod: false }),
        })}
      />
    );

    expect(screen.getByTestId('tab-live')).toBeInTheDocument();
    expect(screen.queryByTestId('tab-vod-movie')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tab-vod-series')).not.toBeInTheDocument();
  });

  it('initializes live group state from playlist channel groups', async () => {
    setupStores();
    render(<M3UGroupFilter {...defaultProps()} />);

    await waitFor(() => {
      expect(screen.getByTestId('live-group-count')).toHaveTextContent('1');
    });
  });

  it('allows child filters to update local state', async () => {
    setupStores();
    render(<M3UGroupFilter {...defaultProps()} />);

    fireEvent.click(screen.getByTestId('live-change-groups'));
    fireEvent.click(screen.getByTestId('vod-change-movie'));
    fireEvent.click(screen.getByTestId('vod-change-series'));

    expect(screen.getByTestId('live-group-count')).toHaveTextContent('1');
    expect(screen.getByTestId('movie-category-count')).toHaveTextContent('1');
    expect(screen.getByTestId('series-category-count')).toHaveTextContent('1');
  });

  it('calls onClose from the modal close button and cancel button', () => {
    const onClose = vi.fn();
    setupStores();
    render(<M3UGroupFilter {...defaultProps({ onClose })} />);

    fireEvent.click(screen.getByTestId('modal-close'));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('saves playlist settings, group settings, refreshes, and closes', async () => {
    const onClose = vi.fn();
    setupStores();
    render(<M3UGroupFilter {...defaultProps({ onClose })} />);

    fireEvent.click(screen.getByTestId('live-toggle-auto'));
    fireEvent.click(screen.getByTestId('vod-toggle-auto-movie'));
    fireEvent.click(screen.getByTestId('vod-toggle-auto-series'));
    fireEvent.click(screen.getByTestId('live-change-groups'));
    fireEvent.click(screen.getByTestId('vod-change-movie'));
    fireEvent.click(screen.getByTestId('vod-change-series'));
    fireEvent.click(screen.getByRole('button', { name: /save and refresh/i }));

    await waitFor(() => {
      expect(API.updatePlaylist).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 1,
          auto_enable_new_groups_live: false,
          auto_enable_new_groups_vod: false,
          auto_enable_new_groups_series: false,
        })
      );
    });

    expect(API.updateM3UGroupSettings).toHaveBeenCalledWith(
      1,
      expect.arrayContaining([
        expect.objectContaining({
          id: 99,
          name: 'Changed Group',
        }),
      ]),
      expect.arrayContaining([
        expect.objectContaining({ id: 55 }),
      ])
    );
    expect(API.refreshPlaylist).toHaveBeenCalledWith(1);
    expect(notifications.show).toHaveBeenCalledTimes(2);
    expect(onClose).toHaveBeenCalled();
  });

  it('keeps the modal open when save fails', async () => {
    const onClose = vi.fn();
    vi.mocked(API.updatePlaylist).mockRejectedValue(new Error('save failed'));
    setupStores();
    render(<M3UGroupFilter {...defaultProps({ onClose })} />);

    fireEvent.click(screen.getByRole('button', { name: /save and refresh/i }));

    await waitFor(() => {
      expect(API.updatePlaylist).toHaveBeenCalled();
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});
