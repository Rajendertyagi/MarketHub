import type { SentimentGroup } from "../types";

interface Props {
  title: string;
  groups: SentimentGroup[];
}

// Source / category breakdown table: per-group item counts and average score.
export function SentimentBreakdown({ title, groups }: Props) {
  if (!groups.length) return null;
  const keyLabel = title === "By Source" ? "Source" : "Category";
  return (
    <div className="panel panel-spaced">
      <div className="panel-header">
        <h2>{title}</h2>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>{keyLabel}</th>
            <th>Items</th>
            <th>Bull</th>
            <th>Neutral</th>
            <th>Bear</th>
            <th>Avg</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={g.key}>
              <td>{g.key}</td>
              <td>{g.n}</td>
              <td>{g.pos}</td>
              <td>{g.neu}</td>
              <td>{g.neg}</td>
              <td className={g.avg > 0 ? "pos" : g.avg < 0 ? "neg" : "neutral"}>
                {g.avg.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
