import { useEffect, useId, useMemo, useRef, useState } from "react";

export interface TerminalSelectOption {
  value: string;
  label: string;
}

interface TerminalSelectProps {
  value: string;
  options: TerminalSelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  name?: string;
  ariaLabel?: string;
  placeholder?: string;
}

export function TerminalSelect({
  value,
  options,
  onChange,
  disabled = false,
  name,
  ariaLabel,
  placeholder = "Select",
}: TerminalSelectProps) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const selected = options.find((option) => option.value === value);
  const label = selected?.label || placeholder;
  const hasOptions = options.length > 0;
  const isDisabled = disabled || !hasOptions;

  const optionIds = useMemo(
    () => options.map((option) => `${id}-${safeOptionId(option.value)}`),
    [id, options],
  );

  useEffect(() => {
    if (!open) return;
    const closeOnPointer = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnPointer);
    return () => document.removeEventListener("pointerdown", closeOnPointer);
  }, [open]);

  const choose = (nextValue: string) => {
    onChange(nextValue);
    setOpen(false);
  };

  const moveSelection = (direction: -1 | 1) => {
    if (!hasOptions) return;
    const next = (selectedIndex + direction + options.length) % options.length;
    onChange(options[next].value);
  };

  return (
    <div className={`terminal-select ${open ? "open" : ""} ${isDisabled ? "disabled" : ""}`} ref={rootRef}>
      {name && <input type="hidden" name={name} value={value} />}
      <button
        type="button"
        className="terminal-select__button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-activedescendant={hasOptions ? optionIds[selectedIndex] : undefined}
        disabled={isDisabled}
        onClick={() => setOpen((next) => !next)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setOpen(false);
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!open) setOpen(true);
            else moveSelection(1);
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) setOpen(true);
            else moveSelection(-1);
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOpen((next) => !next);
          }
        }}
      >
        <span>{label}</span>
        <i aria-hidden="true" />
      </button>
      {open && (
        <div className="terminal-select__menu" id={`${id}-listbox`} role="listbox" aria-label={ariaLabel}>
          {options.map((option, index) => (
            <button
              type="button"
              key={option.value}
              id={optionIds[index]}
              role="option"
              aria-selected={option.value === value}
              className={option.value === value ? "selected" : ""}
              onClick={() => choose(option.value)}
            >
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function safeOptionId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}
