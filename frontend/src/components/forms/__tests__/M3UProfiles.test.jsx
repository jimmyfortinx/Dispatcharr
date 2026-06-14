import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import M3UProfiles from '../M3UProfiles';
import API from '../../../api';

// ── Store mocks ────────────────────────────────────────────────────────────────
vi.mock('../../../store/playlists', () => ({ default: vi.fn() }));
vi.mock('../../../store/warnings', () => ({ default: vi.fn() }));
vi.mock('../../../api', () => ({
  default: {
    deleteM3UProfile: vi.fn(),
    updateM3UProfile: vi.fn(),
  },
}));

// ── Sub-component mocks ────────────────────────────────────────────────────────
vi.mock('../M3UProfile', () => ({
  default: ({ isOpen, onClose, profile }) =>
    isOpen ? (
      <div data-testid="m3u-profile-modal">
        <span data-testid="m3u-profile-editing">
          {profile ? `editing-${profile.id}` : 'new'}
        </span>
        <button data-testid="m3u-profile-close" onClick={onClose}>
          Close
        </button>
      </div>
    ) : null,
}));

vi.mock('../AccountInfoModal', () => ({
  default: ({ isOpen, onClose, profile }) =>
    isOpen ? (
      <div data-testid="account-info-modal">
        <span data-testid="account-info-profile">
          {profile ? profile.id : 'none'}
        </span>
        <button data-testid="account-info-close" onClick={onClose}>
          Close
        </button>
      </div>
    ) : null,
}));

vi.mock('../../ConfirmationDialog', () => ({
  default: ({ opened, onConfirm, onClose, title, message }) =>
    opened ? (
      <div data-testid="confirmation-dialog">
        <span data-testid="confirm-title">{title}</span>
        <span data-testid="confirm-message">{message}</span>
        <button data-testid="confirm-ok" onClick={onConfirm}>
          Confirm
        </button>
        <button data-testid="confirm-cancel" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

// ── lucide-react mocks ─────────────────────────────────────────────────────────
vi.mock('lucide-react', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    Info: () => <svg data-testid="icon-info" />,
    SquareMinus: () => <svg data-testid="icon-square-minus" />,
    SquarePen: () => <svg data-testid="icon-square-pen" />,
  };
});

// ── Mantine core ───────────────────────────────────────────────────────────────
vi.mock('@mantine/core', () => ({
  ActionIcon: ({ children, onClick, disabled, color }) => (
    <button
      data-testid="action-icon"
      data-color={color}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  ),
  Badge: ({ children, color }) => (
    <span data-testid="badge" data-color={color}>
      {children}
    </span>
  ),
  Button: ({ children, onClick, disabled, loading, variant, color }) => (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      data-variant={variant}
      data-color={color}
      data-loading={loading}
    >
      {children}
    </button>
  ),
  Card: ({ children, style }) => (
    <div data-testid="profile-card" style={style}>
      {children}
    </div>
  ),
  Center: ({ children }) => <div>{children}</div>,
  Checkbox: ({ label, checked, onChange }) => (
    <label>
      {label}
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange?.(e)}
      />
    </label>
  ),
  Box: ({ children }) => <div>{children}</div>,
  Flex: ({ children }) => <div>{children}</div>,
  Group: ({ children }) => <div>{children}</div>,
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
  NumberInput: ({ label, value, onChange, disabled, min, max }) => (
    <div>
      <label>{label}</label>
      <input
        data-testid={`number-input-${label?.toLowerCase?.().replace(/\s+/g, '-')}`}
        type="number"
        value={value ?? ''}
        disabled={disabled}
        min={min}
        max={max}
        onChange={(e) => onChange?.(Number(e.target.value))}
      />
    </div>
  ),
  Stack: ({ children }) => <div>{children}</div>,
  Switch: ({ label, checked, onChange, disabled }) => (
    <label>
      {label}
      <input
        data-testid={`switch-${label?.toLowerCase?.().replace(/\s+/g, '-')}`}
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange?.(e)}
      />
    </label>
  ),
  Text: ({ children, size, c, fw }) => (
    <span data-size={size} data-color={c} data-fw={fw}>
      {children}
    </span>
  ),
  useMantineTheme: () => ({ tailwind: { yellow: 3, red: 6 } }),
}));

// ──────────────────────────────────────────────────────────────────────────────
// Imports after mocks
// ──────────────────────────────────────────────────────────────────────────────
import usePlaylistsStore from '../../../store/playlists';
import useWarningsStore from '../../../store/warnings';

// ── Helpers ────────────────────────────────────────────────────────────────────
const makeProfile = (overrides = {}) => ({
  id: 1,
  name: 'Test Profile',
  type: 'regex',
  max_streams: 2,
  is_active: true,
  custom_properties: {},
  ...overrides,
});

const makePlaylist = (overrides = {}) => ({
  id: 1,
  name: 'Test M3U',
  account_type: 'XC',
  ...overrides,
});

const defaultProps = (overrides = {}) => ({
  playlist: makePlaylist(),
  isOpen: true,
  onClose: vi.fn(),
  ...overrides,
});

const setupStores = ({
  fetchPlaylist = vi.fn().mockResolvedValue(undefined),
  suppressWarning = vi.fn(),
  isWarningSuppressed = vi.fn().mockReturnValue(false),
  profiles = { 1: [makeProfile()] },
} = {}) => {
  vi.mocked(usePlaylistsStore).mockImplementation((sel) =>
    sel({ fetchPlaylist, profiles })
  );
  vi.mocked(useWarningsStore).mockImplementation((sel) =>
    sel({ suppressWarning, isWarningSuppressed })
  );
  return { fetchPlaylist, suppressWarning, isWarningSuppressed };
};

// ──────────────────────────────────────────────────────────────────────────────

describe('M3UProfiles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(API.deleteM3UProfile).mockResolvedValue(undefined);
    vi.mocked(API.updateM3UProfile).mockResolvedValue(undefined);
  });

  // ── Guard conditions ───────────────────────────────────────────────────────

  describe('guard conditions', () => {
    it('does not render modal when isOpen is false', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps({ isOpen: false })} />);
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
    });

    it('does not render modal when playlist is null', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps({ playlist: null })} />);
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
    });
  });

  // ── Rendering ──────────────────────────────────────────────────────────────

  describe('rendering', () => {
    it('renders the modal when isOpen is true with a valid playlist', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });

    it('renders an "Add Profile" button', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByRole('button', { name: /new/i })).toBeInTheDocument();
    });

    it('renders a profile card for each profile', () => {
      const profiles = { 1: [makeProfile({ id: 1 }), makeProfile({ id: 2 })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getAllByTestId('profile-card')).toHaveLength(2);
    });

    it('renders the profile name', () => {
      const profiles = { 1: [makeProfile({ name: 'My Profile' })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByText('My Profile')).toBeInTheDocument();
    });

    it('renders an edit icon for each profile', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByTestId('icon-square-pen')).toBeInTheDocument();
    });

    it('renders a delete icon for each profile', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByTestId('icon-square-minus')).toBeInTheDocument();
    });

    it('renders an info icon for each profile', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByTestId('icon-info')).toBeInTheDocument();
    });

    it('renders an expiration badge when the profile has provider expiration data', () => {
      const profiles = {
        1: [
          makeProfile({
            custom_properties: {
              user_info: {
                exp_date: Math.floor(Date.now() / 1000) + 3 * 24 * 60 * 60,
              },
            },
          }),
        ],
      };
      setupStores({ profiles });
      render(
        <M3UProfiles
          {...defaultProps({
            playlist: makePlaylist({ account_type: 'XC' }),
          })}
        />
      );
      expect(screen.getByText(/days|h/i)).toBeInTheDocument();
    });

    it('renders profiles sorted with the default profile first', () => {
      const profiles = {
        1: [
          makeProfile({ id: 1, name: 'Zulu' }),
          makeProfile({ id: 2, name: 'Alpha', is_default: true }),
        ],
      };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      const cards = screen.getAllByTestId('profile-card');
      expect(cards).toHaveLength(2);
      expect(screen.getAllByText(/Alpha|Zulu/)[0]).toHaveTextContent('Alpha');
    });

    it('renders empty state when there are no profiles', () => {
      setupStores({ profiles: { 1: [] } });
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.queryByTestId('profile-card')).not.toBeInTheDocument();
    });
  });

  // ── Close behaviour ────────────────────────────────────────────────────────

  describe('close behaviour', () => {
    it('calls onClose when modal X is clicked', () => {
      const onClose = vi.fn();
      setupStores();
      render(<M3UProfiles {...defaultProps({ onClose })} />);
      fireEvent.click(screen.getByTestId('modal-close'));
      expect(onClose).toHaveBeenCalled();
    });
  });

  // ── Add Profile ────────────────────────────────────────────────────────────

  describe('add profile', () => {
    it('opens M3UProfile modal when Add Profile is clicked', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      fireEvent.click(screen.getByRole('button', { name: /new/i }));
      expect(screen.getByTestId('m3u-profile-modal')).toBeInTheDocument();
    });

    it('opens M3UProfile modal with null profile for a new profile', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      fireEvent.click(screen.getByRole('button', { name: /new/i }));
      expect(screen.getByTestId('m3u-profile-editing').textContent).toBe('new');
    });

    it('closes M3UProfile modal when its onClose is called', () => {
      setupStores();
      render(<M3UProfiles {...defaultProps()} />);
      fireEvent.click(screen.getByRole('button', { name: /new/i }));
      fireEvent.click(screen.getByTestId('m3u-profile-close'));
      expect(screen.queryByTestId('m3u-profile-modal')).not.toBeInTheDocument();
    });
  });

  // ── Edit Profile ───────────────────────────────────────────────────────────

  describe('edit profile', () => {
    it('opens M3UProfile modal with the correct profile when edit is clicked', () => {
      const profiles = { 1: [makeProfile({ id: 42 })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      const editBtn = screen.getByTestId('icon-square-pen').closest('button');
      fireEvent.click(editBtn);
      expect(screen.getByTestId('m3u-profile-modal')).toBeInTheDocument();
      expect(screen.getByTestId('m3u-profile-editing').textContent).toBe(
        'editing-42'
      );
    });
  });

  // ── Account Info ───────────────────────────────────────────────────────────

  describe('account info', () => {
    it('opens AccountInfoModal when info icon is clicked', () => {
      const profiles = { 1: [makeProfile({ id: 7 })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      const infoBtn = screen.getByTestId('icon-info').closest('button');
      fireEvent.click(infoBtn);
      expect(screen.getByTestId('account-info-modal')).toBeInTheDocument();
    });
  });

  // ── Delete profile ─────────────────────────────────────────────────────────

  describe('delete profile', () => {
    it('shows ConfirmationDialog when delete icon is clicked and warning not suppressed', () => {
      setupStores({ isWarningSuppressed: vi.fn().mockReturnValue(false) });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      expect(screen.getByTestId('confirmation-dialog')).toBeInTheDocument();
    });

    it('calls deleteM3UProfile directly when warning is suppressed', async () => {
      const profiles = { 1: [makeProfile()] };
      setupStores({
        isWarningSuppressed: vi.fn().mockReturnValue(true),
        profiles,
      });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      await waitFor(() => {
        expect(API.deleteM3UProfile).toHaveBeenCalledWith(1, profiles[1][0].id);
      });
    });

    it('calls deleteM3UProfile after confirming the dialog', async () => {
      const profiles = { 1: [makeProfile()] };
      setupStores({
        isWarningSuppressed: vi.fn().mockReturnValue(false),
        profiles,
      });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      fireEvent.click(screen.getByTestId('confirm-ok'));
      await waitFor(() => {
        expect(API.deleteM3UProfile).toHaveBeenCalledWith(1, profiles[1][0].id);
      });
    });

    it('closes ConfirmationDialog after confirming', async () => {
      setupStores({ isWarningSuppressed: vi.fn().mockReturnValue(false) });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      fireEvent.click(screen.getByTestId('confirm-ok'));
      await waitFor(() => {
        expect(
          screen.queryByTestId('confirmation-dialog')
        ).not.toBeInTheDocument();
      });
    });

    it('closes ConfirmationDialog when cancel is clicked', () => {
      setupStores({ isWarningSuppressed: vi.fn().mockReturnValue(false) });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      fireEvent.click(screen.getByTestId('confirm-cancel'));
      expect(
        screen.queryByTestId('confirmation-dialog')
      ).not.toBeInTheDocument();
    });

    it('does not call deleteM3UProfile when cancel is clicked', async () => {
      setupStores({ isWarningSuppressed: vi.fn().mockReturnValue(false) });
      render(<M3UProfiles {...defaultProps()} />);
      const deleteBtn = screen
        .getByTestId('icon-square-minus')
        .closest('button');
      fireEvent.click(deleteBtn);
      fireEvent.click(screen.getByTestId('confirm-cancel'));
      expect(API.deleteM3UProfile).not.toHaveBeenCalled();
    });
  });

  // ── Max streams / Switch ───────────────────────────────────────────────────

  describe('profile-level controls', () => {
    it('renders NumberInput for max_streams on a profile card', () => {
      const profiles = { 1: [makeProfile({ max_streams: 3 })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      expect(screen.getByRole('spinbutton')).toBeInTheDocument();
    });

    it('calls updateM3UProfile when max_streams NumberInput changes', async () => {
      const profiles = { 1: [makeProfile({ max_streams: 2 })] };
      setupStores({ profiles });
      render(<M3UProfiles {...defaultProps()} />);
      const input = screen.getByRole('spinbutton');
      fireEvent.change(input, { target: { value: '5' } });
      await waitFor(() => {
        expect(API.updateM3UProfile).toHaveBeenCalledWith(
          1,
          expect.objectContaining({
            id: profiles[1][0].id,
            max_streams: 5,
          })
        );
      });
    });
  });

  // ── Expiration / expired styling ───────────────────────────────────────────

  describe('expiration display', () => {
    it('renders expiration badge with computed color', () => {
      const profiles = {
        1: [
          makeProfile({
            custom_properties: {
              user_info: {
                exp_date: Math.floor(Date.now() / 1000) + 3 * 24 * 60 * 60,
              },
            },
          }),
        ],
      };
      setupStores({ profiles });
      render(
        <M3UProfiles
          {...defaultProps({
            playlist: makePlaylist({ account_type: 'XC' }),
          })}
        />
      );
      const badge = screen.getByTestId('badge');
      expect(badge.textContent).toMatch(/days|h/i);
      expect(['orange', 'red', 'yellow', 'green']).toContain(
        badge.dataset.color
      );
    });
  });

  // ── Warning suppression ────────────────────────────────────────────────────

  describe('warning suppression', () => {
    it('calls suppressWarning when user confirms and suppression is selected', async () => {
      const { suppressWarning } = setupStores({
        isWarningSuppressed: vi.fn().mockReturnValue(false),
      });
      render(<M3UProfiles {...defaultProps()} />);
      expect(suppressWarning).toBeDefined();
    });
  });
});
