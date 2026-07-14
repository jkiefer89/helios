import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TerminalSelect } from "./TerminalSelect";

const options = [
  { value: "one", label: "One" },
  { value: "two", label: "Two" },
  { value: "three", label: "Three" },
];

afterEach(cleanup);

describe("TerminalSelect keyboard contract", () => {
  it("moves an active option without committing until Enter", () => {
    const onChange = vi.fn();
    render(<TerminalSelect ariaLabel="Evidence source" value="one" options={options} onChange={onChange} />);
    const control = screen.getByRole("combobox", { name: "Evidence source" });

    fireEvent.click(control);
    fireEvent.keyDown(control, { key: "ArrowDown" });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(control, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith("two");
    expect(control.getAttribute("aria-expanded")).toBe("false");
  });

  it("commits a pointer selection once and leaves the menu closed", () => {
    const onChange = vi.fn();
    render(
      <label>
        Target
        <TerminalSelect ariaLabel="Analysis target" value="one" options={options} onChange={onChange} />
      </label>,
    );
    const control = screen.getByRole("combobox", { name: "Analysis target" });

    fireEvent.click(control);
    fireEvent.pointerDown(screen.getByRole("option", { name: "Two" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("two");
    expect(control.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("supports Home, End, and Escape like a native select", () => {
    const onChange = vi.fn();
    render(<TerminalSelect ariaLabel="Model" value="two" options={options} onChange={onChange} />);
    const control = screen.getByRole("combobox", { name: "Model" });

    fireEvent.keyDown(control, { key: "End" });
    fireEvent.keyDown(control, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith("three");

    fireEvent.click(control);
    fireEvent.keyDown(control, { key: "Escape" });
    expect(control.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes without committing when focus leaves with Tab", () => {
    const onChange = vi.fn();
    render(
      <div>
        <TerminalSelect ariaLabel="Model" value="one" options={options} onChange={onChange} />
        <button type="button">Next field</button>
      </div>,
    );
    const control = screen.getByRole("combobox", { name: "Model" });

    fireEvent.click(control);
    fireEvent.keyDown(control, { key: "ArrowDown" });
    fireEvent.keyDown(control, { key: "Tab" });
    fireEvent.blur(control, { relatedTarget: screen.getByRole("button", { name: "Next field" }) });

    expect(control.getAttribute("aria-expanded")).toBe("false");
    expect(onChange).not.toHaveBeenCalled();
  });
});
