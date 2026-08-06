$(function () {
    const slotsUrl = $('script[data-slots-url]').data('slots-url');
    const $dateInput = $('#date-input');
    const $staffSelect = $('#staff-select');
    const $slotsContainer = $('#slots-container');
    const $slotIsoInput = $('#slot-iso-input');
    const $submitBtn = $('#submit-btn');

    function getSelectedServiceId() {
        return $('input[name="service_id"]:checked').val();
    }

    function resetSlotSelection() {
        $slotIsoInput.val('');
        $submitBtn.prop('disabled', true).text('Select a time slot to continue');
        $('.slot-btn').removeClass('is-selected');
    }

    function loadSlots() {
        const serviceId = getSelectedServiceId();
        const date = $dateInput.val();
        const staffId = $staffSelect.val();

        resetSlotSelection();

        if (!serviceId || !date) {
            $slotsContainer.html('<p class="slots-placeholder">Select a service first to see available times.</p>');
            return;
        }

        $slotsContainer.html('<p class="slots-placeholder">Loading available times…</p>');

        $.ajax({
            url: slotsUrl,
            data: { service_id: serviceId, date: date, staff_id: staffId },
            method: 'GET',
            dataType: 'json',
        }).done(function (response) {
            renderSlots(response.slots);
        }).fail(function () {
            $slotsContainer.html('<p class="slots-placeholder slots-placeholder--error">Could not load times. Please try again.</p>');
        });
    }

    function renderSlots(slots) {
        if (!slots || slots.length === 0) {
            $slotsContainer.html('<p class="slots-placeholder">No available times on this date — try another day.</p>');
            return;
        }

        const $grid = $('<div class="slots-grid-inner"></div>');
        slots.forEach(function (slot) {
            const $btn = $('<button type="button" class="slot-btn"></button>')
                .text(slot.time)
                .attr('data-iso', slot.iso);
            $grid.append($btn);
        });
        $slotsContainer.empty().append($grid);
    }

    // Event: clicking a rendered slot button selects it.
    $slotsContainer.on('click', '.slot-btn', function () {
        $('.slot-btn').removeClass('is-selected');
        $(this).addClass('is-selected');
        $slotIsoInput.val($(this).data('iso'));
        $submitBtn.prop('disabled', false).text('Confirm booking');
    });

    // Re-fetch slots whenever service, date, or staff choice changes.
    $(document).on('change', 'input[name="service_id"]', loadSlots);
    $dateInput.on('change', loadSlots);
    $staffSelect.on('change', loadSlots);

    // Load once on page entry if a service radio is already checked
    // (e.g. browser back/forward restoring form state).
    if (getSelectedServiceId()) {
        loadSlots();
    }

    // Prevent double submissions by disabling the submit button on form submission
    $('#booking-form').on('submit', function () {
        $submitBtn.prop('disabled', true).text('Processing booking...');
    });
});