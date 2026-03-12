# monitoring/services/scoring.py
import requests
from bs4 import BeautifulSoup
import time

class SiteScoringService:
    """
    Service to evaluate a website and calculate various quality scores
    """
    
    def __init__(self, url):
        self.url = url if url.startswith(('http://', 'https://')) else f'https://{url}'
        self.http_url = f'http://{url}' if not url.startswith(('http://', 'https://')) else url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; CodeCheckerBot/1.1)'
        })
        self.metrics = {}
        
    def evaluate(self):
        """Run all evaluations and return composite scores"""
        
        # Performance metrics
        self._measure_performance()
        
        # SEO evaluation
        self._evaluate_seo()
        
        # Security evaluation
        self._evaluate_security()
        
        # Calculate scores
        scores = self._calculate_composite_scores()
        return scores
    
    def _measure_performance(self):
        """Measure page load times and content size"""
        start_time = time.time()
        try:
            response = self.session.get(self.url, timeout=10)
            ttfb = time.time() - start_time
            
            self.metrics.update({
                'status_code': response.status_code,
                'ttfb_ms': int(ttfb * 1000),
                'content_size_kb': len(response.content) / 1024,
                'page_load_time_ms': int((time.time() - start_time) * 1000),
                'has_ssl': self.url.startswith('https://')
            })
            
        except Exception as e:
            self.metrics.update({
                'status_code': 500,
                'ttfb_ms': None,
                'content_size_kb': 0,
                'page_load_time_ms': None,
                'error': str(e)
            })
    
    def _evaluate_seo(self):
        """Evaluate SEO factors like meta tags, headings"""
        try:
            response = self.session.get(self.url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            seo_metrics = {
                'has_title': bool(soup.find('title')),
                'has_meta_description': bool(soup.find('meta', attrs={'name': 'description'})),
                'has_meta_keywords': bool(soup.find('meta', attrs={'name': 'keywords'})),
                'has_canonical': bool(soup.find('link', attrs={'rel': 'canonical'})),
                'h1_count': len(soup.find_all('h1')),
                'img_with_alt': len([img for img in soup.find_all('img') if img.get('alt')]),
                'img_total': len(soup.find_all('img')),
            }
            
            self.metrics.update(seo_metrics)
            
        except Exception as e:
            self.metrics['seo_error'] = str(e)
    
    def _evaluate_security(self):
        """Check security headers and HTTPS configuration"""
        try:
            response = self.session.get(self.url, timeout=10)
            headers = response.headers
            
            security_metrics = {
                'has_hsts': 'strict-transport-security' in headers,
                'has_csp': 'content-security-policy' in headers,
                'has_xframe': 'x-frame-options' in headers,
                'has_xss_protection': 'x-xss-protection' in headers,
                'has_referrer_policy': 'referrer-policy' in headers,
            }
            
            self.metrics.update(security_metrics)
            
        except Exception as e:
            self.metrics['security_error'] = str(e)
    
    def _calculate_composite_scores(self):
        """Convert raw metrics to normalized scores (0-100)"""
        
        scores = {}
        
        # Performance score (lower is better)
        if self.metrics.get('ttfb_ms'):
            # TTFB < 200ms = 100, > 1000ms = 0
            ttfb = self.metrics['ttfb_ms']
            scores['performance'] = max(0, min(100, 100 - ((ttfb - 200) / 8)))
        else:
            scores['performance'] = 0
        
        # SEO score (based on best practices)
        seo_score = 0
        if self.metrics.get('has_title'):
            seo_score += 20
        if self.metrics.get('has_meta_description'):
            seo_score += 20
        if self.metrics.get('h1_count') == 1:
            seo_score += 20
        if self.metrics.get('img_total', 0) > 0:
            img_alt_ratio = self.metrics.get('img_with_alt', 0) / self.metrics.get('img_total', 1)
            seo_score += int(img_alt_ratio * 20)
        scores['seo'] = seo_score
        
        # Security score
        security_score = 0
        if self.metrics.get('has_ssl'):
            security_score += 30
        if self.metrics.get('has_hsts'):
            security_score += 20
        if self.metrics.get('has_csp'):
            security_score += 20
        if self.metrics.get('has_xframe'):
            security_score += 15
        if self.metrics.get('has_xss_protection'):
            security_score += 15
        scores['security'] = min(100, security_score)
        
        # Availability score based on status code
        if self.metrics.get('status_code') == 200:
            scores['availability'] = 100
        elif self.metrics.get('status_code') and 200 <= self.metrics['status_code'] < 400:
            scores['availability'] = 80
        else:
            scores['availability'] = 0
        
        # Overall composite score (weighted average)
        weights = {
            'performance': 0.25,
            'seo': 0.25,
            'security': 0.25,
            'availability': 0.25
        }
        
        overall = sum(scores[k] * weights[k] for k in scores if k in weights)
        
        return {
            **scores,
            'overall': overall,
            'metrics': self.metrics
        }
