import './views.css';

/**
 * Data: the Data workspace draws its navigation (sources, queries, reports) into this element, as it did in the old side bar.
 * Choosing Data in the activity bar also opens the Data tab, which is where the navigation comes from.
 */
export default function DataView({host}: {host: (node: HTMLDivElement | null) => void}) {
  return <div className="data-sidebar-host" ref={host} />;
}
