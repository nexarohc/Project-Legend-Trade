import logo from "../assets/logo.svg";

/**
 * The chrome above the terminal.
 *
 * Deliberately thin: on a trading surface every pixel of chrome is a pixel not
 * showing the market, so this is one row with a brand, two destinations and the
 * account.
 */
export default function TopBar({ view, onNavigate, user, onSignOut, onHome }) {
  const tabs = [
    ["terminal", "Terminal"],
    ["account", "Account"],
  ];

  return (
    <div
      className="flex items-center justify-between px-3 h-11 shrink-0
                 border-b border-term-border bg-term-surface select-none"
    >
      <button
        onClick={onHome}
        className="flex items-center gap-2 group"
        title="Back to the home page"
      >
        <img src={logo} alt="" className="h-6 w-6 rounded" />
        <span className="font-display font-semibold text-sm text-term-text tracking-tight
                         group-hover:text-white transition-colors">
          Legend Trade
        </span>
      </button>

      <div className="flex items-center gap-1">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => onNavigate(key)}
            className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${
              view === key
                ? "bg-term-raised text-term-text"
                : "text-term-muted hover:text-term-text"
            }`}
          >
            {label}
          </button>
        ))}

        {/* Only rendered when authentication is enforced; a local single-user
            run has no account to sign out of. */}
        {user && (
          <div className="flex items-center gap-2 ml-2 pl-3 border-l border-term-border">
            <span
              className="text-[11px] text-term-muted max-w-[11rem] truncate"
              title={user.email}
            >
              {user.display_name || user.email}
            </span>
            <button onClick={onSignOut} className="btn-term">
              Sign out
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
