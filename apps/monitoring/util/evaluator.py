# evaluation.py
from django.utils import timezone
from datetime import timedelta
from apps.monitoring.models import Site, SiteSnapshot, ScreenshotComparison, SiteScore
import json

class SiteEvaluator:
    """
    Evaluation class for a given domain/site
    Loads latest screenshot comparison and latest site score
    """
    
    def __init__(self, domain):
        """
        Initialize evaluator with a domain name
        
        Args:
            domain: Domain name (e.g., "example.com")
        """
        self.domain = domain.lower().strip()
        self.site = None
        self.latest_comparison = None
        self.latest_score = None
        self.baseline_snapshot = None
        self.error = None
        
        # Load the site
        try:
            self.site = Site.objects.get(name=self.domain)
        except Site.DoesNotExist:
            self.error = f"Site '{domain}' not found"
            return
        
        # Load data
        self._load_latest_comparison()
        self._load_latest_score()
        self._load_baseline()
    
    def _load_latest_comparison(self):
        """Load the most recent screenshot comparison for this site"""
        if not self.site:
            return
        
        self.latest_comparison = ScreenshotComparison.objects.filter(
            site=self.site
        ).select_related(
            'previous_snapshot', 'current_snapshot'
        ).order_by('-created_at').first()
    
    def _load_latest_score(self):
        """Load the most recent site score for this site"""
        if not self.site:
            return
        
        self.latest_score = SiteScore.objects.filter(
            site=self.site
        ).select_related('snapshot').order_by('-calculated_at').first()
    
    def _load_baseline(self):
        """Load the baseline snapshot for this site"""
        if not self.site:
            return
        
        self.baseline_snapshot = self.site.snapshots.filter(
            is_baseline=True
        ).first()
    
    def is_valid(self):
        """Check if evaluator has valid data"""
        return self.site is not None and self.error is None
    
    def has_comparison(self):
        """Check if there's a latest comparison"""
        return self.latest_comparison is not None
    
    def has_score(self):
        """Check if there's a latest score"""
        return self.latest_score is not None
    
    def get_comparison_summary(self):
        """Get a summary of the latest comparison"""
        if not self.latest_comparison:
            return {
                'exists': False,
                'message': 'No comparison data available'
            }
        
        comp = self.latest_comparison
        return {
            'exists': True,
            'id': comp.id,
            'created_at': comp.created_at.isoformat(),
            'ssim_score': comp.ssim_score,
            'percent_difference': comp.percent_difference,
            'changed_pixels': comp.changed_pixels,
            'total_pixels': comp.total_pixels,
            'previous_snapshot': {
                'id': comp.previous_snapshot.id,
                'taken_at': comp.previous_snapshot.taken_at.isoformat(),
                'is_baseline': comp.previous_snapshot.is_baseline
            },
            'current_snapshot': {
                'id': comp.current_snapshot.id,
                'taken_at': comp.current_snapshot.taken_at.isoformat()
            },
            'has_heatmap': bool(comp.heatmap),
            'has_diff': bool(comp.diff_image),
            'heatmap_url': comp.heatmap.url if comp.heatmap else None,
            'diff_url': comp.diff_image.url if comp.diff_image else None
        }
    
    def get_score_summary(self):
        """Get a summary of the latest score"""
        if not self.latest_score:
            return {
                'exists': False,
                'message': 'No score data available'
            }
        
        score = self.latest_score
        return {
            'exists': True,
            'id': score.id,
            'calculated_at': score.calculated_at.isoformat(),
            'overall_score': score.overall_score,
            'performance_score': score.performance_score,
            'seo_score': score.seo_score,
            'security_score': score.security_score,
            'availability_score': score.availability_score,
            'metrics': {
                'page_load_time_ms': score.page_load_time_ms,
                'ttfb_ms': score.ttfb_ms,
                'content_size_kb': score.content_size_kb,
                'has_ssl': score.has_ssl
            },
            'snapshot_id': score.snapshot.id if score.snapshot else None,
            'snapshot_taken_at': score.snapshot.taken_at.isoformat() if score.snapshot else None
        }
    
    def get_baseline_summary(self):
        """Get a summary of the baseline snapshot"""
        if not self.baseline_snapshot:
            return {
                'exists': False,
                'message': 'No baseline snapshot found'
            }
        
        baseline = self.baseline_snapshot
        return {
            'exists': True,
            'id': baseline.id,
            'taken_at': baseline.taken_at.isoformat(),
            'http_status_code': baseline.http_status_code,
            'content_length': baseline.content_length,
            'has_screenshot': bool(baseline.screenshot),
            'screenshot_url': baseline.screenshot.url if baseline.screenshot else None
        }
    
    def get_site_info(self):
        """Get basic site information"""
        if not self.site:
            return {'error': self.error}
        
        return {
            'id': self.site.id,
            'name': self.site.name,
            'server': self.site.server.name if self.site.server else None,
            'server_id': self.site.server.id if self.site.server else None,
            'resolved_ip': self.site.resolved_ip,
            'is_active': self.site.is_active,
            'continuous_monitoring': self.site.continuous_monitoring,
            'monitoring_frequency': self.site.monitoring_frequency,
            'created_at': self.site.created_at.isoformat(),
            'last_monitored': self.site.last_monitored.isoformat() if self.site.last_monitored else None
        }
    
    def get_monitoring_text(self, compact=True):
        """
        Generate a one-line monitoring text for Zulip
        
        Returns a tuple with (bool, text)
        bool is False if SSIM is fail OR if baseline status differs from latest status
        text format: "| [site.com](http://.../sites/XX/) | baseline_status→current_status | ssim(ok/warning/fail) | score | change%"
        """
        if not self.is_valid():
            return (False, f"| [{self.domain}](http://dontdeletezoltan.man-1.solus.stage.town/sites/) | Error: {self.error}")
        
        # Get baseline status code
        baseline_status = self.baseline_snapshot.http_status_code if self.baseline_snapshot else None
        
        # Get latest status code from the most recent snapshot
        latest_snapshot = self.site.snapshots.order_by('-taken_at').first()
        latest_status = latest_snapshot.http_status_code if latest_snapshot else None
        
        # Get comparison data
        if self.has_comparison():
            comp = self.latest_comparison
            ssim_score = comp.ssim_score
            percent_difference = comp.percent_difference
        else:
            ssim_score = 0
            percent_difference = 0
        
        # Get score data
        if self.has_score():
            score = self.latest_score
            overall_score = score.overall_score or 0
        else:
            overall_score = 0
        
        # Evaluate SSIM status
        if ssim_score >= 0.98:
            ssim_status = "ok"
            ssim_pass = True
        elif ssim_score >= 0.90:
            ssim_status = "warning"
            ssim_pass = True
        else:
            ssim_status = "fail"
            ssim_pass = False
        
        # Check if status codes match (if both exist)
        status_match = True
        if baseline_status is not None and latest_status is not None:
            status_match = (baseline_status == latest_status)
        
        # Determine overall pass/fail
        overall_pass = ssim_pass and status_match
        
        # Format baseline status
        if baseline_status:
            if baseline_status == 200:
                baseline_display = f"✅{baseline_status}"
            elif 300 <= baseline_status < 400:
                baseline_display = f"⚠️{baseline_status}"
            elif baseline_status >= 400:
                baseline_display = f"❌{baseline_status}"
            else:
                baseline_display = str(baseline_status)
        else:
            baseline_display = "N/A"
        
        # Format current status
        if latest_status:
            if latest_status == 200:
                current_display = f"✅{latest_status}"
            elif 300 <= latest_status < 400:
                current_display = f"⚠️{latest_status}"
            elif latest_status >= 400:
                current_display = f"❌{latest_status}"
            else:
                current_display = str(latest_status)
        else:
            current_display = "N/A"
        
        # Create site link
        site_link = f"[{self.domain}](http://dontdeletezoltan.man-1.solus.stage.town/sites/{self.site.id}/)"
        
        # Format the text with site link, baseline→current status, and values
        # Order: site_link | baseline→current | ssim(ok/warning/fail) | score | change%
        text = (
            f"| {site_link} | "
            f"{baseline_display}→{current_display} | "
            f"{ssim_score:.4f}({ssim_status}) | "
            f"{overall_score:.1f} | "
            f"{percent_difference:.1f}%"
        )
        
        return (overall_pass, text)