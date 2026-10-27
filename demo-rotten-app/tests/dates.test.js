const { formatDate, addDays, subtractMonths, daysBetween } = require("../src/dates");

describe("date formatting", () => {
  it("formats an ISO date string", () => {
    expect(formatDate("2024-03-05")).toBe("2024-03-05");
  });

  it("formats a Date object", () => {
    expect(formatDate(new Date(Date.UTC(2024, 0, 15)))).toBe("2024-01-15");
  });

  it("honours an explicit format string", () => {
    expect(formatDate("2024-03-05T10:00:00.000Z", "YYYY/MM/DD")).toBe("2024/03/05");
  });
});

describe("date arithmetic", () => {
  it("adds days", () => {
    expect(addDays("2024-03-05", 10)).toBe("2024-03-15");
  });

  it("subtracts months", () => {
    expect(subtractMonths("2024-03-31", 1)).toBe("2024-02-29");
  });

  it("counts whole days between two dates", () => {
    expect(daysBetween("2024-03-05", "2024-03-12")).toBe(7);
  });

  it("returns a negative count when the range is reversed", () => {
    expect(daysBetween("2024-03-12", "2024-03-05")).toBe(-7);
  });
});