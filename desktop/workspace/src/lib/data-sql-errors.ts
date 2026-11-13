/** Plain-language versions of the messages databases return for Advanced SQL. Anything not recognised is returned unchanged. */
const quote = (name?: string) => name ? '"' + name.replace(/^["'`]|["'`]$/g, '') + '" ' : '';
const RULES: {test: RegExp; say: (match: RegExpMatchArray) => string}[] = [
  {test: /ambiguous column name:?\s*(\S+)?|column reference "?([^"\s]+)"? is ambiguous|column '([^']+)' in field list is ambiguous/i,
    say: match => 'The column ' + quote(match[1] || match[2] || match[3]) + 'matches more than one table. Put the table name in front of it, like orders.id.'},
  {test: /no such table:?\s*(\S+)|relation "([^"]+)" does not exist|table '([^']+)' doesn't exist/i,
    say: match => 'There is no table ' + quote(match[1] || match[2] || match[3]) + 'in this source. Check the spelling against the Tables list.'},
  {test: /no such column:?\s*(\S+)|column "([^"]+)" does not exist|unknown column '([^']+)'/i,
    say: match => 'There is no column ' + quote(match[1] || match[2] || match[3]) + 'in these tables. Check the spelling against the table.'},
  {test: /syntax error(?: at or near "([^"]*)")?|near "([^"]*)": syntax error|you have an error in your sql syntax[^']*'([^']*)'/i,
    say: match => {
      const near = (match[1] || match[2] || match[3] || '').trim().slice(0, 40);
      return 'The SQL could not be read' + (near ? ' near "' + near + '"' : '') + '. Look for a missing comma, quote, bracket or keyword.';
    }},
  {test: /timeout|timed out|canceling statement|max_execution_time|maximum statement|interrupted/i,
    say: () => 'This query took too long and was stopped. Try a filter, fewer columns or a smaller time range.'},
  {test: /read-?only|permission denied|command denied|not allowed|only (?:read|select)/i,
    say: () => 'Advanced SQL can only read data. Remove anything that changes data, such as insert, update or delete.'},
];

export function plainSqlError(message: string | null | undefined): string {
  const text = (message || '').trim();
  if (!text) return '';
  for (const rule of RULES) {
    const match = text.match(rule.test);
    if (match) return rule.say(match);
  }
  return text;
}
