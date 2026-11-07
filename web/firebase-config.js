// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
import { getAnalytics } from "firebase/analytics";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
// For Firebase JS SDK v7.20.0 and later, measurementId is optional
const firebaseConfig = {
  apiKey: "AIzaSyD3bKPbi_VzbkLhGq0ssZwhi7HnvBn8Zp4",
  authDomain: "unsaid-devfest.firebaseapp.com",
  projectId: "unsaid-devfest",
  storageBucket: "unsaid-devfest.firebasestorage.app",
  messagingSenderId: "799163399675",
  appId: "1:799163399675:web:82ff5e00debd34473d1f5e",
  measurementId: "G-YKX741TT2G"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
const analytics = getAnalytics(app);