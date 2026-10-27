const { mergeConfig, mergeAll } = require("../src/objects");

describe("object merging", () => {
  it("merges nested overrides onto the base", () => {
    const base = { server: { host: "localhost", port: 8080 }, debug: false };
    const overrides = { server: { port: 9090 } };

    expect(mergeConfig(base, overrides)).toEqual({
      server: { host: "localhost", port: 9090 },
      debug: false,
    });
  });

  it("does not mutate the base object", () => {
    const base = { server: { port: 8080 } };
    mergeConfig(base, { server: { port: 9090 } });

    expect(base.server.port).toBe(8080);
  });

  it("merges layers from left to right", () => {
    const result = mergeAll([{ a: 1, nested: { x: 1 } }, { b: 2 }, { nested: { y: 2 } }]);

    expect(result).toEqual({ a: 1, b: 2, nested: { x: 1, y: 2 } });
  });
});