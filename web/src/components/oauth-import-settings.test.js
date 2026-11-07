import { describe, expect, test } from "bun:test";
import { buildImportSettings, initialImportSettings, prefixedImportName } from "./oauth-import-settings.jsx";

describe("OAuth import name prefix", () => {
  test("defaults to no prefix and preserves existing names", () => {
    const form = initialImportSettings();
    expect(form.namePrefix).toBe("");
    const settings = buildImportSettings(form, ["First", "Second"]);
    expect(settings.name_prefix).toBe("");
    expect(settings.names).toEqual(["First", "Second"]);
  });

  test("sends the prefix separately without modifying the editable names", () => {
    const names = [" First ", "Second"];
    const settings = buildImportSettings({ ...initialImportSettings(), namePrefix: "  批次-  " }, names);
    expect(settings.name_prefix).toBe("批次-");
    expect(settings.names).toEqual(["First", "Second"]);
    expect(names).toEqual([" First ", "Second"]);
    expect(settings.names.map((name) => prefixedImportName(name, settings.name_prefix)))
      .toEqual(["批次-First", "批次-Second"]);
  });

  test("preview trims whitespace and does not insert a separator", () => {
    expect(prefixedImportName(" First ", " batch ")).toBe("batchFirst");
    expect(prefixedImportName(" First ", "   ")).toBe("First");
    expect(prefixedImportName(" First ")).toBe("First");
  });

  test("changing or clearing the prefix never accumulates it", () => {
    const names = ["First"];
    for (const prefix of ["batch-", "other-", ""]) {
      const settings = buildImportSettings({ ...initialImportSettings(), namePrefix: prefix }, names);
      expect(prefixedImportName(settings.names[0], settings.name_prefix)).toBe(`${prefix}First`);
    }
    expect(names).toEqual(["First"]);
  });

  test("rejects overlong prefixes and overlong combined names", () => {
    expect(() => buildImportSettings({ ...initialImportSettings(), namePrefix: "x".repeat(101) }, ["A"]))
      .toThrow("账号名称前缀最多 100 个字符");
    expect(() => buildImportSettings({ ...initialImportSettings(), namePrefix: "batch-" }, ["Good", "x".repeat(95)]))
      .toThrow("添加前缀后的每个账号名称最多 100 个字符");
  });

  test("a prefix does not make an empty base name valid", () => {
    expect(() => buildImportSettings({ ...initialImportSettings(), namePrefix: "batch-" }, [" "]))
      .toThrow("每个账号名称须为 1–100 个字符");
  });

  test("accepts exactly 100 Unicode characters like the backend", () => {
    const settings = buildImportSettings({ ...initialImportSettings(), namePrefix: "🚀".repeat(99) }, ["甲"]);
    expect([...prefixedImportName(settings.names[0], settings.name_prefix)]).toHaveLength(100);
  });
});
