// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

vi.mock("three", () => {
  class Vector2 {
    set() {}
  }

  class ShaderMaterial {
    uniforms: Record<string, { value: unknown }>;

    constructor(options: { uniforms: Record<string, { value: unknown }> }) {
      this.uniforms = options.uniforms;
    }

    dispose() {}
  }

  return {
    WebGLRenderer: class {
      setClearColor() {}
      setPixelRatio() {}
      setSize() {}
      render() {}
      dispose() {}
    },
    Scene: class {
      add() {}
    },
    OrthographicCamera: class {},
    PlaneGeometry: class {},
    ShaderMaterial,
    Mesh: class {},
    Vector2,
  };
});

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => [],
    })),
  );
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("App workbench tabs", () => {
  it("keeps keyframes primary and reveals JSON/deploy tools through tabs", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("tab", { name: "关键帧" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "关键帧" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table", { name: "关键帧列表" })).toBeInTheDocument();
    expect(screen.queryByLabelText("dance.json 编辑器")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "推到设备 + 播放音乐" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("dance.json 编辑器")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "下发" }));
    expect(screen.getByRole("tab", { name: "下发" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "推到设备 + 播放音乐" })).toBeInTheDocument();
  });
});
