from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Listing, Booking, Payment
from .serializers import ListingSerializer, BookingSerializer, PaymentSerializer
import os
import requests
from decimal import Decimal


CHAPA_SECRET_KEY = os.getenv('CHAPA_SECRET_KEY')
CHAPA_BASE_URL = 'https://api.chapa.co/v1'
HEADERS = {
    'Authorization': f'Bearer {CHAPA_SECRET_KEY}',
    'Content-Type': 'application/json'
}


class ListingViewSet(viewsets.ModelViewSet):
    queryset = Listing.objects.all()
    serializer_class = ListingSerializer


class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.objects.all()
    serializer_class = BookingSerializer

    @action(detail=True, methods=['post'], url_path='initiate-payment')
    def initiate_payment(self, request, pk=None):
        """
        Initiates a payment using the Chapa API
        POST /bookings/{id}/initiate-payment/
        """
        booking = self.get_object()
        amount = booking.total_price()
        user = booking.user

        data = {
            "amount": str(amount),
            "currency": "ETB",  # Adjust currency
            "email": user.email,
            "first_name": user.first_name or "Guest",
            "last_name": user.last_name or "",
            "tx_ref": f"booking-{booking.id}",
            "callback_url": "http://localhost:8000/api/bookings/verify-payment/",
            "return_url": "http://localhost:3000/payment-success",  # frontend redirect
            "customization": {
                "title": "Booking Payment",
                "description": f"Payment for booking {booking.id}"
            }
        }

        response = requests.post(f"{CHAPA_BASE_URL}/transaction/initialize", json=data, headers=HEADERS)

        if response.status_code != 200 or response.json().get('status') != 'success':
            return Response({"error": "Failed to initiate payment"}, status=status.HTTP_502_BAD_GATEWAY)

        chapa_data = response.json()['data']
        Payment.objects.update_or_create(
            booking=booking,
            defaults={
                'transaction_id': chapa_data['tx_ref'],
                'amount': Decimal(amount),
                'status': 'Pending',
            }
        )

        return Response({
            'payment_url': chapa_data['checkout_url'],
            'transaction_id': chapa_data['tx_ref'],
            'message': 'Payment initiated successfully'
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='verify-payment')
    def verify_payment(self, request):
        """
        Verifies payment using Chapa API
        POST /bookings/verify-payment/
        Payload: { "transaction_id": "tx_ref" }
        """
        tx_ref = request.data.get('transaction_id')

        if not tx_ref:
            return Response({"error": "Transaction ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        verify_url = f"{CHAPA_BASE_URL}/transaction/verify/{tx_ref}"
        response = requests.get(verify_url, headers=HEADERS)

        if response.status_code != 200 or response.json().get('status') != 'success':
            return Response({"error": "Failed to verify payment"}, status=status.HTTP_502_BAD_GATEWAY)

        chapa_data = response.json()['data']
        status_result = chapa_data.get('status')

        try:
            payment = Payment.objects.get(transaction_id=tx_ref)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)

        if status_result == 'success':
            payment.status = 'Completed'
        else:
            payment.status = 'Failed'
        payment.save()

        return Response({
            "transaction_id": tx_ref,
            "status": payment.status,
            "amount": str(payment.amount),
            "message": "Payment verified and status updated"
        }, status=status.HTTP_200_OK)


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
