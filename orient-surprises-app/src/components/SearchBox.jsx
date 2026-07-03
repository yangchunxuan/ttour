"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./SearchBox.module.css";

export default function SearchBox() {
  const [query, setQuery] = useState("");
  const router = useRouter();

  const handleSearch = (e) => {
    e.preventDefault();
    if (query.trim()) {
      router.push(`/tours?city=${encodeURIComponent(query.trim())}`);
    } else {
      router.push(`/tours`);
    }
  };

  return (
    <form onSubmit={handleSearch} className={styles.searchBox}>
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={styles.searchIcon}>
        <circle cx="11" cy="11" r="8"></circle>
        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
      </svg>
      <input 
        type="text" 
        placeholder="Where do you want to go? (e.g. Shanghai)" 
        className={styles.searchInput}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <button type="submit" className="btn btn-primary">Search</button>
    </form>
  );
}
