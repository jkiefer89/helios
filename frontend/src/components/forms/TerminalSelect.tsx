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
  const [activeIndex, setActiveIndex] = useState(0);
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

  const moveActive = (direction: -1 | 1) => {
    if (!hasOptions) return;
    setActiveIndex((current) => (current + direction + options.length) % options.length);
  };

  return (
    <div
      className={`terminal-select ${open ? "open" : ""} ${isDisabled ? "disabled" : ""}`}
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      {name && <input type="hidden" name={name} value={value} />}
      <button
        type="button"
        role="combobox"
        className="terminal-select__button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-autocomplete="none"
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-activedescendant={open && hasOptions ? optionIds[activeIndex] : undefined}
        disabled={isDisabled}
        onClick={() => {
          setActiveIndex(selectedIndex);
          setOpen((next) => !next);
        }}
        onKeyDown={(event) => {
          if (event.key === "Tab") {
            setOpen(false);
            return;
          }
          if (event.key === "Escape") {
            setOpen(false);
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!open) {
              setActiveIndex(selectedIndex);
              setOpen(true);
            }
            else moveActive(1);
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) {
              setActiveIndex(selectedIndex);
              setOpen(true);
            }
            else moveActive(-1);
            return;
          }
          if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            if (!open) setOpen(true);
            setActiveIndex(event.key === "Home" ? 0 : options.length - 1);
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (open && options[activeIndex]) choose(options[activeIndex].value);
            else setOpen(true);
          }
        }}
      >
        <span>{label}</span>
        <i aria-hidden="true" />
      </button>
      {open && (
        <div className="terminal-select__menu" id={`${id}-listbox`} role="listbox" aria-label={ariaLabel}>
          {options.map((option, index) => (
            <div
              key={option.value}
              id={optionIds[index]}
              role="option"
              tabIndex={-1}
              aria-selected={option.value === value}
              className={`${option.value === value ? "selected" : ""} ${index === activeIndex ? "active" : ""}`.trim()}
              onPointerMove={() => setActiveIndex(index)}
              onPointerDown={(event) => {
                // Prevent the enclosing form label from re-activating the
                // combobox after the option closes the menu.
                event.preventDefault();
                event.stopPropagation();
                choose(option.value);
              }}
            >
              <span>{option.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function safeOptionId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, "-");
}
