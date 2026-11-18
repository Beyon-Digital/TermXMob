import './views.css';

/** Sites: saved sites arrive with the browser sites backend. Until then there is nothing to list and nothing to do. */
export default function SitesView() {
  return <p className="view-empty view-empty-padded">No saved sites yet.</p>;
}
