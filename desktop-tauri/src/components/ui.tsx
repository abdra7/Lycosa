import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Icon } from "./icons";
import { useToast } from "../state/toast";

type Variant = "filled" | "tonal" | "outlined" | "text" | "danger";

export function Button({
  variant = "filled",
  busy = false,
  icon,
  children,
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean; icon?: string }) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} ${className}`}
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spinner small" /> : icon ? <Icon name={icon} size={16} /> : null}
      {children}
    </button>
  );
}

export function IconButton({
  icon,
  label,
  danger,
  busy,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string; danger?: boolean; busy?: boolean }) {
  return (
    <button
      type="button"
      className={`icon-btn ${danger ? "danger" : ""}`}
      title={label}
      aria-label={label}
      {...rest}
      disabled={rest.disabled || busy}
    >
      {busy ? <span className="spinner small" /> : <Icon name={icon} />}
    </button>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return <span className={`spinner ${small ? "small" : ""}`} role="progressbar" aria-label="Loading" />;
}

export function LinearProgress() {
  return <div className="linear" role="progressbar" aria-label="Working" />;
}

export function Card({ title, icon, children, className = "", actions }: { title?: ReactNode; icon?: string; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {title !== undefined && (
        <div className="card-title">
          {icon && <Icon name={icon} />}
          <h2>{title}</h2>
          <span className="spacer" />
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Field({
  label,
  help,
  error,
  children,
}: {
  label: string;
  help?: ReactNode;
  error?: string | null;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {error ? <span className="small error-text">{error}</span> : help ? <span className="help">{help}</span> : null}
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  help,
  error,
  type = "text",
  multiline,
  rows,
  mono,
  onEnter,
  ...rest
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  help?: ReactNode;
  error?: string | null;
  type?: string;
  multiline?: boolean;
  rows?: number;
  mono?: boolean;
  onEnter?: () => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value">) {
  return (
    <Field label={label} help={help} error={error}>
      {(id) =>
        multiline ? (
          <textarea
            id={id}
            className={`textarea ${mono ? "mono" : ""}`}
            value={value}
            rows={rows ?? 3}
            onChange={(e) => onChange(e.target.value)}
            disabled={rest.disabled}
            placeholder={rest.placeholder}
            autoFocus={rest.autoFocus}
            spellCheck={!mono}
          />
        ) : (
          <input
            id={id}
            className="input"
            type={type}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && onEnter) {
                e.preventDefault();
                onEnter();
              }
            }}
            {...rest}
          />
        )
      }
    </Field>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  onChange,
  options,
  disabled,
  help,
  placeholder,
}: {
  label: string;
  value: T | "";
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  disabled?: boolean;
  help?: ReactNode;
  placeholder?: string;
}) {
  return (
    <Field label={label} help={help}>
      {(id) => (
        <select id={id} className="select" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as T)}>
          {placeholder !== undefined && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

export function Dialog({
  title,
  onClose,
  children,
  actions,
  width = 560,
  dismissable = true,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  width?: number;
  dismissable?: boolean;
}) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dismissable) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, dismissable]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && dismissable && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} style={{ maxWidth: width }}>
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
        </div>
        <div className="dialog-body">{children}</div>
        {actions && <div className="dialog-actions">{actions}</div>}
      </div>
    </div>
  );
}

/** A yes/no question; resolves true on confirm. */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger,
  onResult,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onResult: (confirmed: boolean) => void;
}) {
  return (
    <Dialog
      title={title}
      onClose={() => onResult(false)}
      width={480}
      actions={
        <>
          <Button variant="text" onClick={() => onResult(false)}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "filled"} onClick={() => onResult(true)} autoFocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p>{message}</p>
    </Dialog>
  );
}

/** Ask a confirmation from an event handler: `if (await confirm(...))`. */
export function useConfirm() {
  const [pending, setPending] = useState<{ props: Omit<Parameters<typeof ConfirmDialog>[0], "onResult">; resolve: (v: boolean) => void } | null>(null);
  const confirm = (props: Omit<Parameters<typeof ConfirmDialog>[0], "onResult">) =>
    new Promise<boolean>((resolve) => setPending({ props, resolve }));
  const element = pending ? (
    <ConfirmDialog
      {...pending.props}
      onResult={(ok) => {
        pending.resolve(ok);
        setPending(null);
      }}
    />
  ) : null;
  return { confirm, element };
}

export function StatusDot({ color, title }: { color: string; title?: string }) {
  return <span className="dot" style={{ background: color }} title={title} aria-hidden={title ? undefined : true} />;
}

export function Chip({ children, small, title }: { children: ReactNode; small?: boolean; title?: string }) {
  return (
    <span className={`chip ${small ? "small" : ""}`} title={title}>
      {children}
    </span>
  );
}

export function Banner({ kind = "info", children }: { kind?: "info" | "warning" | "error"; children: ReactNode }) {
  return (
    <div className={`banner ${kind}`} role={kind === "error" ? "alert" : "status"}>
      <Icon name={kind === "info" ? "info" : "warning"} />
      <div className="grow">{children}</div>
    </div>
  );
}

export function useCopy() {
  const toast = useToast();
  return async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.show("Copied to clipboard");
    } catch {
      toast.show("Could not copy; select the text instead", "error");
    }
  };
}

export function CodeBlock({ text, copyable }: { text: string; copyable?: boolean }) {
  const copy = useCopy();
  return (
    <div className="col" style={{ gap: 2 }}>
      <pre className="code mono">{text}</pre>
      {copyable && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <Button variant="text" icon="copy" onClick={() => copy(text)}>
            Copy
          </Button>
        </div>
      )}
    </div>
  );
}

/** Long output in a bounded, scrollable block with a copy button. */
export function OutputBlock({ text, isError }: { text: string; isError?: boolean }) {
  const copy = useCopy();
  return (
    <div className={`output-block ${isError ? "error" : ""}`}>
      <span className="copy-corner">
        <IconButton icon="copy" label="Copy" onClick={() => copy(text)} />
      </span>
      {text}
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string; icon?: string }[] }) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} size={16} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Bar({ percent, label }: { percent: number | null | undefined; label: string }) {
  const value = percent == null || !Number.isFinite(percent) ? null : Math.max(0, Math.min(100, percent));
  return (
    <div className="row" style={{ gap: 12 }}>
      <span className="small muted" style={{ width: 130 }}>
        {label}
      </span>
      <div className="bar grow" role="meter" aria-label={label} aria-valuenow={value ?? undefined} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${value ?? 0}%` }} />
      </div>
      <span style={{ width: 44 }}>{value == null ? "—" : `${value.toFixed(0)}%`}</span>
    </div>
  );
}

/** Close a popup when the user clicks elsewhere. */
export function useOutsideClick<T extends HTMLElement>(onOutside: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutside();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onOutside]);
  return ref;
}
