import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FormatToggle } from "../src/components/gsv/FormatToggle";

describe("FormatToggle", () => {
  it("renders three buttons (markdown / text / json)", () => {
    render(<FormatToggle format="markdown" onChange={() => {}} />);
    expect(screen.getByTestId("gsv-format-toggle__markdown")).toBeInTheDocument();
    expect(screen.getByTestId("gsv-format-toggle__text")).toBeInTheDocument();
    expect(screen.getByTestId("gsv-format-toggle__json")).toBeInTheDocument();
  });

  it("invokes onChange with the chosen format", async () => {
    const onChange = vi.fn();
    render(<FormatToggle format="markdown" onChange={onChange} />);
    await userEvent.click(screen.getByTestId("gsv-format-toggle__json"));
    expect(onChange).toHaveBeenCalledWith("json");
    await userEvent.click(screen.getByTestId("gsv-format-toggle__text"));
    expect(onChange).toHaveBeenCalledWith("text");
  });

  it("marks the currently active format with data-active=true", () => {
    render(<FormatToggle format="text" onChange={() => {}} />);
    expect(screen.getByTestId("gsv-format-toggle__text")).toHaveAttribute(
      "data-active",
      "true",
    );
    expect(screen.getByTestId("gsv-format-toggle__markdown")).toHaveAttribute(
      "data-active",
      "false",
    );
  });
});
