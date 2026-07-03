"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./Navbar.module.css";

export default function Navbar() {
  const [isScrolled, setIsScrolled] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const handleScroll = () => {
      if (window.scrollY > 50) {
        setIsScrolled(true);
      } else {
        setIsScrolled(false);
      }
    };
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  // Close menu on route change
  useEffect(() => {
    setIsMenuOpen(false);
  }, [pathname]);

  return (
    <nav className={`${styles.nav} ${isScrolled ? styles.scrolled : ""}`}>
      <div className={`container ${styles.navInner}`}>
        <Link href="/" className={styles.logo}>
          <img 
            src="/images/logo/报价单模版_logo.png" 
            alt="Orient Surprises Travel" 
            className={styles.logoImg} 
            onError={(e) => {
              e.target.style.display = 'none';
              e.target.nextSibling.style.display = 'block';
            }}
          />
          <span className={styles.logoText} style={{ display: "none" }}>
            Orient Surprises Travel
          </span>
        </Link>
        
        <button 
          className={`${styles.hamburger} ${isMenuOpen ? styles.open : ""}`}
          onClick={() => setIsMenuOpen(!isMenuOpen)}
          aria-label="Toggle menu"
        >
          <span></span><span></span><span></span>
        </button>

        <ul className={`${styles.links} ${isMenuOpen ? styles.mobileOpen : ""}`}>
          <li>
            <Link href="/" className={`${styles.link} ${pathname === "/" ? styles.active : ""}`}>
              Home
            </Link>
          </li>
          <li>
            <Link href="/tours" className={`${styles.link} ${pathname === "/tours" ? styles.active : ""}`}>
              Tours
            </Link>
          </li>
          <li>
            <Link href="/about" className={`${styles.link} ${pathname === "/about" ? styles.active : ""}`}>
              About Us
            </Link>
          </li>
          <li>
            <Link href="/contact" className={styles.cta}>
              Enquire Now
            </Link>
          </li>
        </ul>
      </div>
    </nav>
  );
}
