const { satisfies, compare, isMajorBehind } = require("../src/versions");

describe("version helpers", () => {
  it("evaluates a semver range", () => {
    expect(satisfies("4.17.21", ">=4.17.21 <5.0.0")).toBe(true);
    expect(satisfies("4.16.0", ">=4.17.21 <5.0.0")).toBe(false);
  });

  it("compares two versions", () => {
    expect(compare("1.2.0", "1.10.0")).toBe(-1);
    expect(compare("2.0.0", "2.0.0")).toBe(0);
  });

  it("detects a major version gap", () => {
    expect(isMajorBehind("1.2.0", "2.0.0")).toBe(true);
    expect(isMajorBehind("1.2.0", "1.9.0")).toBe(false);
  });
});