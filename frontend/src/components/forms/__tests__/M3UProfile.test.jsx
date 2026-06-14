import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import M3UProfile from '../M3UProfile';
import API from '../../../api';

vi.mock('../../../WebSocket', () => ({
  useWebSocket: vi.fn(() => [true, vi.fn(), null]),
}));

vi.mock('../../../api', () => ({
  default: {
    queryStreams: vi.fn(),
    addM3UProfile: vi.fn(),
    updateM3UProfile: vi.fn(),
  },
}));

vi.mock('lucide-react', () => ({
  TriangleAlert: () => <svg data-testid="icon-triangle-alert" />,
}));

vi.mock('@mantine/dates', () => ({
  DateTimePicker: ({ label, value, onChange, placeholder }) => (
    <div data-testid="date-time-picker">
      <label>
        {label}
        <input
          data-testid="date-time-input"
          value={value ?? ''}
          onChange={(e) => onChange?.(e.target.value)}
          placeholder={placeholder}
        />
      </label>
    </div>
  ),
}));

vi.mock('@mantine/core', () => {
  const Grid = ({ children }) => <div data-testid="grid">{children}</div>;
  Grid.Col = ({ children, span }) => (
    <div data-testid="grid-col" data-span={span}>
      {children}
    </div>
  );

  return {
    Alert: ({ children, title }) => (
      <div data-testid="alert">
        <span data-testid="alert-title">{title}</span>
        {children}
      </div>
    ),
    Badge: ({ children, color }) => (
      <span data-testid="badge" data-color={color}>
        {children}
      </span>
    ),
    Button: ({ children, onClick, disabled, type = 'button' }) => (
      <button type={type} onClick={onClick} disabled={disabled}>
        {children}
      </button>
    ),
    Flex: ({ children }) => <div>{children}</div>,
    Grid,
    Modal: ({ children, opened, onClose, title }) =>
      opened ? (
        <div data-testid="modal">
          <div data-testid="modal-title">{title}</div>
          <button data-testid="modal-close" onClick={onClose}>
            ×
          </button>
          {children}
        </div>
      ) : null,
    NumberInput: ({ label, value, onChange, placeholder }) => (
      <label>
        {label}
        <input
          aria-label={label}
          type="number"
          value={value ?? ''}
          onChange={(e) => onChange?.(Number(e.target.value))}
          placeholder={placeholder}
        />
      </label>
    ),
    Paper: ({ children }) => <div>{children}</div>,
    SegmentedControl: ({ value, onChange, data }) => (
      <div data-testid="segmented-control">
        {data.map((item) => (
          <button
            key={item.value}
            type="button"
            data-testid={`segment-${item.value}`}
            data-active={value === item.value}
            onClick={() => onChange?.(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
    ),
    Text: ({ children }) => <span>{children}</span>,
    Textarea: ({ label, error, minRows, maxRows, autosize, ...props }) => (
      <label>
        {label}
        <textarea aria-label={label} {...props} />
        {error && <span data-testid="field-error">{error}</span>}
      </label>
    ),
    TextInput: ({ label, error, ...props }) => (
      <label>
        {label}
        <input aria-label={label} {...props} />
        {error && <span data-testid="field-error">{error}</span>}
      </label>
    ),
    Title: ({ children }) => <h2>{children}</h2>,
  };
});

const makeM3U = (overrides = {}) => ({
  id: 1,
  name: 'Test M3U',
  url: 'http://example.com/playlist.m3u',
  username: 'user1',
  password: 'pass1',
  account_type: 'M3U',
  custom_properties: {
    max_streams: 1,
    profile: null,
    ...overrides.custom_properties,
  },
  ...overrides,
});

const makeProfile = (overrides = {}) => ({
  id: 10,
  name: 'Test Profile',
  type: 'regex',
  search_pattern: 'old',
  replace_pattern: 'new',
  max_streams: 2,
  exp_date: null,
  custom_properties: {},
  is_default: false,
  ...overrides,
});

const defaultProps = (overrides = {}) => ({
  m3u: makeM3U(),
  isOpen: true,
  onClose: vi.fn(),
  profile: null,
  ...overrides,
});

describe('M3UProfile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(API.queryStreams).mockResolvedValue({
      results: [{ url: 'http://example.com/stream1' }],
    });
    vi.mocked(API.addM3UProfile).mockResolvedValue(undefined);
    vi.mocked(API.updateM3UProfile).mockResolvedValue(undefined);
  });

  it('renders the base form for a standard profile', () => {
    render(<M3UProfile {...defaultProps()} />);
    expect(screen.getByTestId('modal')).toBeInTheDocument();
    expect(screen.getByTestId('modal-title')).toHaveTextContent('M3U Profile');
    expect(screen.getByLabelText('Name')).toBeInTheDocument();
    expect(screen.getByLabelText('Max Streams')).toBeInTheDocument();
    expect(screen.getByTestId('date-time-picker')).toBeInTheDocument();
    expect(screen.getByLabelText('Notes')).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/enter a sample url to test with/i)
    ).toBeInTheDocument();
  });

  it('renders XC simple mode inputs and hides local expiration', () => {
    render(
      <M3UProfile
        {...defaultProps({
          m3u: makeM3U({ account_type: 'XC' }),
        })}
      />
    );
    expect(screen.getByTestId('segmented-control')).toBeInTheDocument();
    expect(screen.getByLabelText('New Username')).toBeInTheDocument();
    expect(screen.getByLabelText('New Password')).toBeInTheDocument();
    expect(screen.queryByTestId('date-time-picker')).not.toBeInTheDocument();
  });

  it('renders default profile controls and resets patterns to defaults', () => {
    render(
      <M3UProfile
        {...defaultProps({
          profile: makeProfile({
            is_default: true,
            search_pattern: 'foo',
            replace_pattern: 'bar',
          }),
        })}
      />
    );

    expect(screen.getByTestId('alert-title')).toHaveTextContent(
      'Default Profile'
    );
    fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }));
    expect(screen.getByLabelText('Search Pattern (Regex)')).toHaveValue(
      '^(.*)$'
    );
    expect(screen.getByLabelText('Replace Pattern')).toHaveValue('$1');
  });

  it('loads a sample stream URL from the API', async () => {
    render(<M3UProfile {...defaultProps()} />);
    await waitFor(() => {
      expect(API.queryStreams).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/enter a sample url to test with/i)
      ).toHaveValue('http://example.com/stream1');
    });
  });

  it('submits a new profile through API.addM3UProfile', async () => {
    const onClose = vi.fn();
    render(<M3UProfile {...defaultProps({ onClose })} />);

    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'New Profile' },
    });
    fireEvent.change(screen.getByLabelText('Search Pattern (Regex)'), {
      target: { value: 'old' },
    });
    fireEvent.change(screen.getByLabelText('Replace Pattern'), {
      target: { value: 'new' },
    });
    fireEvent.change(screen.getByLabelText('Notes'), {
      target: { value: 'some note' },
    });

    fireEvent.click(screen.getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(API.addM3UProfile).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          name: 'New Profile',
          search_pattern: 'old',
          replace_pattern: 'new',
          custom_properties: expect.objectContaining({
            notes: 'some note',
          }),
          exp_date: null,
        })
      );
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('submits an existing profile through API.updateM3UProfile', async () => {
    const profile = makeProfile({ id: 42, name: 'Original' });
    render(<M3UProfile {...defaultProps({ profile })} />);

    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Updated Profile' },
    });
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));

    await waitFor(() => {
      expect(API.updateM3UProfile).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          id: 42,
          name: 'Updated Profile',
          search_pattern: 'old',
          replace_pattern: 'new',
          exp_date: null,
        })
      );
    });
  });

  it('shows local validation errors for XC simple mode when credentials are empty', async () => {
    render(
      <M3UProfile
        {...defaultProps({
          m3u: makeM3U({ account_type: 'XC' }),
        })}
      />
    );

    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'XC Profile' },
    });
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));

    expect(await screen.findByText('New username is required')).toBeInTheDocument();
    expect(screen.getByText('New password is required')).toBeInTheDocument();
    expect(API.updateM3UProfile).not.toHaveBeenCalled();
  });
});
