import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import './styles/tokens.css';
import './styles/base.css';
import './styles/layout.css';
import './styles/components.css';
import './styles/charts.css';

const root = document.getElementById('root');
if (root === null) throw new Error('the page has no #root element');
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
