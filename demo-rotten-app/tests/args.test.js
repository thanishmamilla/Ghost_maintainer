const { parseArgs } = require("../src/args");

describe("argument parsing", () => {
  it("reads a string flag", () => {
    expect(parseArgs(["--target", "release"]).target).toBe("release");
  });

  it("reads boolean flags with their aliases", () => {
    const parsed = parseArgs(["-v", "--help"]);

    expect(parsed.verbose).toBe(true);
    expect(parsed.help).toBe(true);
  });

  it("defaults booleans to false and target to null", () => {
    const parsed = parseArgs([]);

    expect(parsed.verbose).toBe(false);
    expect(parsed.help).toBe(false);
    expect(parsed.target).toBeNull();
  });

  it("lists unknown flags in sorted order", () => {
    expect(parseArgs(["--zeta", "--alpha"]).flags).toEqual(["alpha", "zeta"]);
  });
});