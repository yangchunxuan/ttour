"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import TourCard from "@/components/TourCard";
import toursData from "@/data/toursData.json";
import styles from "./page.module.css";

function ToursContent() {
  const searchParams = useSearchParams();
  const initialCity = searchParams.get("city") || "";

  const [filteredTours, setFilteredTours] = useState(toursData);
  const [filters, setFilters] = useState({
    city: initialCity,
    duration: ""
  });

  useEffect(() => {
    let result = toursData;

    if (filters.city) {
      const searchCity = filters.city.toLowerCase();
      result = result.filter(tour => 
        tour.cities.some(c => c.toLowerCase().includes(searchCity)) ||
        tour.title.toLowerCase().includes(searchCity)
      );
    }

    if (filters.duration) {
      if (filters.duration === "1-5") {
        result = result.filter(tour => tour.durationDays <= 5);
      } else if (filters.duration === "6-10") {
        result = result.filter(tour => tour.durationDays >= 6 && tour.durationDays <= 10);
      } else if (filters.duration === "11+") {
        result = result.filter(tour => tour.durationDays >= 11);
      }
    }

    setFilteredTours(result);
  }, [filters]);

  const handleFilterChange = (e) => {
    const { name, value } = e.target;
    setFilters(prev => ({ ...prev, [name]: value }));
  };

  return (
    <>
      <Navbar />

      {/* Page Header */}
      <header className={styles.pageHeader}>
        <div className={styles.headerOverlay}></div>
        <div className={`container ${styles.headerContent}`}>
          <h1>Curated China Journeys</h1>
          <p>Find the perfect starting point for your tailor-made experience.</p>
        </div>
      </header>

      <main className="section-padding">
        <div className="container">
          
          {/* Filters */}
          <div className={styles.filterBar}>
            <div className={styles.filterGroup}>
              <label>Destination</label>
              <select name="city" value={filters.city} onChange={handleFilterChange} className={styles.select}>
                <option value="">All Destinations</option>
                <option value="beijing">Beijing</option>
                <option value="shanghai">Shanghai</option>
                <option value="zhangjiajie">Zhangjiajie</option>
                <option value="chengdu">Chengdu</option>
                <option value="guangzhou">Guangzhou</option>
              </select>
            </div>
            <div className={styles.filterGroup}>
              <label>Duration</label>
              <select name="duration" value={filters.duration} onChange={handleFilterChange} className={styles.select}>
                <option value="">Any Duration</option>
                <option value="1-5">1 - 5 Days</option>
                <option value="6-10">6 - 10 Days</option>
                <option value="11+">11+ Days</option>
              </select>
            </div>
            <button 
              className="btn btn-outline" 
              onClick={() => setFilters({city: "", duration: ""})}
              style={{ alignSelf: "flex-end", borderColor: "var(--color-navy)", color: "var(--color-navy)" }}
            >
              Reset
            </button>
          </div>

          <div className={styles.resultsCount}>
            Showing {filteredTours.length} {filteredTours.length === 1 ? 'journey' : 'journeys'}
          </div>

          {/* Tour Grid */}
          <div className={styles.toursGrid}>
            {filteredTours.length > 0 ? (
              filteredTours.map(tour => (
                <div key={tour.id} className="fade-in">
                  <TourCard tour={tour} />
                </div>
              ))
            ) : (
              <div className={styles.noResults}>
                <h3>No journeys match your criteria.</h3>
                <p>Try adjusting your filters or contact us to customize a completely new route for you.</p>
              </div>
            )}
          </div>
        </div>
      </main>

      <Footer />
    </>
  );
}

export default function ToursPage() {
  return (
    <Suspense fallback={<div>Loading tours...</div>}>
      <ToursContent />
    </Suspense>
  );
}
